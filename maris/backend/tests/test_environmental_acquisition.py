"""Unit and integration test suite for Stage C1 — Automatic Environmental Acquisition.

Tests:
- Spatial framing & buffer calculation (bbox, polygon, invalid coordinate handling).
- Temporal framing (lookback/forward hours).
- Provider request construction.
- Mocked ERA5 acquisition and validation.
- Mocked CMEMS acquisition and validation.
- AssetRegistry registration (WindField, CurrentField).
- Partial failure handling (ERA5-only failure, CMEMS-only failure, both-failure).
- Validation rejection handling (invalid NetCDF -> not registered as usable).
- API route integration via TestClient.
- All tests are strictly offline with zero external network calls.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
import netCDF4 as nc
import numpy as np

from app.acquisition.base import AcquisitionError
from app.acquisition.providers.cmems import CmemsAcquisitionProvider
from app.acquisition.providers.era5 import Era5AcquisitionProvider
from app.acquisition.registry import InMemoryAssetRegistry
from app.core.config import Settings
from app.main import app
from app.models.common import (
    AssetType,
    BBoxAreaOfInterest,
    BoundingBox,
    PolygonAreaOfInterest,
    TimeWindow,
)
from app.models.environment import CurrentField, WindField
from app.models.satellite import SatelliteScene
from app.services.environmental_acquisition import (
    EnvironmentalAcquisitionError,
    ProviderAcquisitionStatus,
    acquire_environmental_data_for_investigation,
    acquire_environmental_data_for_scene,
    derive_environmental_bbox,
    derive_environmental_time_window,
)


def _make_mock_era5_nc(path: Path) -> None:
    """Create a minimal valid ERA5 wind NetCDF file matching A4.3 validator expectations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with nc.Dataset(str(path), "w", format="NETCDF4") as ds:
        ds.createDimension("time", 6)
        ds.createDimension("latitude", 5)
        ds.createDimension("longitude", 5)

        time_var = ds.createVariable("time", "i4", ("time",))
        time_var.units = "hours since 1900-01-01 00:00:00"
        time_var.calendar = "proleptic_gregorian"
        time_var[:] = [1085784 + i for i in range(6)]

        lat_var = ds.createVariable("latitude", "f4", ("latitude",))
        lat_var.units = "degrees_north"
        lat_var[:] = [52.0, 51.75, 51.5, 51.25, 51.0]

        lon_var = ds.createVariable("longitude", "f4", ("longitude",))
        lon_var.units = "degrees_east"
        lon_var[:] = [2.0, 2.25, 2.5, 2.75, 3.0]

        u_var = ds.createVariable("u10", "f4", ("time", "latitude", "longitude"), fill_value=np.float32(9.96921e36))
        u_var.units = "m s**-1"
        u_var[:] = np.full((6, 5, 5), 5.0, dtype=np.float32)

        v_var = ds.createVariable("v10", "f4", ("time", "latitude", "longitude"), fill_value=np.float32(9.96921e36))
        v_var.units = "m s**-1"
        v_var[:] = np.full((6, 5, 5), -3.0, dtype=np.float32)


def _make_mock_cmems_nc(path: Path) -> None:
    """Create a minimal valid CMEMS current NetCDF file matching A4.4 validator expectations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with nc.Dataset(str(path), "w", format="NETCDF4") as ds:
        ds.createDimension("time", 2)
        ds.createDimension("depth", 1)
        ds.createDimension("latitude", 5)
        ds.createDimension("longitude", 5)

        time_var = ds.createVariable("time", "i4", ("time",))
        time_var.units = "days since 1950-01-01 00:00:00"
        time_var.calendar = "standard"
        time_var[:] = [27180, 27181]

        depth_var = ds.createVariable("depth", "f4", ("depth",))
        depth_var.units = "m"
        depth_var.positive = "down"
        depth_var[:] = [0.494025]

        lat_var = ds.createVariable("latitude", "f4", ("latitude",))
        lat_var.units = "degrees_north"
        lat_var[:] = [51.0, 51.25, 51.5, 51.75, 52.0]

        lon_var = ds.createVariable("longitude", "f4", ("longitude",))
        lon_var.units = "degrees_east"
        lon_var[:] = [2.0, 2.25, 2.5, 2.75, 3.0]

        uo_var = ds.createVariable("uo", "f4", ("time", "depth", "latitude", "longitude"), fill_value=np.float32(9.96921e36))
        uo_var.units = "m s-1"
        uo_var[:] = np.full((2, 1, 5, 5), 0.2, dtype=np.float32)

        vo_var = ds.createVariable("vo", "f4", ("time", "depth", "latitude", "longitude"), fill_value=np.float32(9.96921e36))
        vo_var.units = "m s-1"
        vo_var[:] = np.full((2, 1, 5, 5), -0.1, dtype=np.float32)


class MockCdsClient:
    def __init__(self, should_fail: bool = False, fail_validation: bool = False) -> None:
        self.should_fail = should_fail
        self.fail_validation = fail_validation
        self.calls: list[dict] = []

    def retrieve(self, dataset: str, request: dict, target: str) -> None:
        self.calls.append({"dataset": dataset, "request": request, "target": target})
        if self.should_fail:
            raise AcquisitionError("Mock CDS network failure")
        target_path = Path(target)
        if self.fail_validation:
            # Write invalid file (e.g. empty or corrupt)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(b"CDF\x01not_a_valid_nc")
        else:
            _make_mock_era5_nc(target_path)


class MockCmemsClient:
    def __init__(self, should_fail: bool = False, fail_validation: bool = False) -> None:
        self.should_fail = should_fail
        self.fail_validation = fail_validation
        self.calls: list[dict] = []

    def subset(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self.should_fail:
            raise AcquisitionError("Mock CMEMS subset error")
        out_dir = Path(kwargs["output_directory"])
        out_file = kwargs.get("output_filename", "cmems_surface_currents.nc")
        target_path = out_dir / out_file
        if self.fail_validation:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(b"CDF\x01corrupted_cmems")
        else:
            _make_mock_cmems_nc(target_path)


class EnvironmentalFramingTests(unittest.TestCase):
    """Test spatial buffering and time-window derivations."""

    def test_derive_bbox_from_bbox_aoi_with_buffer(self) -> None:
        aoi = BBoxAreaOfInterest(bbox=BoundingBox(west=2.0, south=40.0, east=4.0, north=42.0))
        buffered = derive_environmental_bbox(aoi, buffer_degrees=0.5)

        self.assertEqual(buffered.kind, "bbox")
        self.assertAlmostEqual(buffered.bbox.west, 1.5)
        self.assertAlmostEqual(buffered.bbox.south, 39.5)
        self.assertAlmostEqual(buffered.bbox.east, 4.5)
        self.assertAlmostEqual(buffered.bbox.north, 42.5)

    def test_derive_bbox_from_polygon_aoi(self) -> None:
        poly = PolygonAreaOfInterest(
            kind="polygon",
            coordinates=[[[8.0, 42.0], [9.0, 42.0], [9.0, 43.0], [8.0, 43.0], [8.0, 42.0]]],
        )
        buffered = derive_environmental_bbox(poly, buffer_degrees=0.25)
        self.assertAlmostEqual(buffered.bbox.west, 7.75)
        self.assertAlmostEqual(buffered.bbox.south, 41.75)
        self.assertAlmostEqual(buffered.bbox.east, 9.25)
        self.assertAlmostEqual(buffered.bbox.north, 43.25)

    def test_derive_bbox_clamps_at_global_bounds(self) -> None:
        aoi = BBoxAreaOfInterest(bbox=BoundingBox(west=-179.9, south=-89.9, east=179.9, north=89.9))
        buffered = derive_environmental_bbox(aoi, buffer_degrees=1.0)
        self.assertAlmostEqual(buffered.bbox.west, -180.0)
        self.assertAlmostEqual(buffered.bbox.south, -90.0)
        self.assertAlmostEqual(buffered.bbox.east, 180.0)
        self.assertAlmostEqual(buffered.bbox.north, 90.0)

    def test_invalid_aoi_rejected(self) -> None:
        with self.assertRaises(EnvironmentalAcquisitionError):
            derive_environmental_bbox(area=BBoxAreaOfInterest(bbox=BoundingBox(west=2.0, south=40.0, east=4.0, north=42.0)), buffer_degrees=-0.1)

    def test_derive_time_window_lookback_forward(self) -> None:
        anchor = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        window = derive_environmental_time_window(anchor, lookback_hours=24.0, forward_hours=6.0)

        expected_start = datetime(2024, 5, 31, 12, 0, tzinfo=timezone.utc)
        expected_end = datetime(2024, 6, 1, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(window.start, expected_start)
        self.assertEqual(window.end, expected_end)

    def test_invalid_lookback_rejected(self) -> None:
        anchor = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        with self.assertRaises(EnvironmentalAcquisitionError):
            derive_environmental_time_window(anchor, lookback_hours=-5.0)


class EnvironmentalAcquisitionOrchestrationTests(unittest.TestCase):
    """Integration tests for orchestrating ERA5 + CMEMS acquisition and validation."""

    def test_both_providers_succeed_and_register(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            settings = Settings(data_dir=tmp_path, cds_api_key="mock", cmems_username="u", cmems_password="p")

            mock_cds = MockCdsClient()
            mock_cmems = MockCmemsClient()
            era5_p = Era5AcquisitionProvider(settings=settings, client=mock_cds)
            cmems_p = CmemsAcquisitionProvider(settings=settings, client=mock_cmems)
            registry = InMemoryAssetRegistry()

            aoi = BBoxAreaOfInterest(bbox=BoundingBox(west=2.0, south=51.0, east=3.0, north=52.0))
            window = TimeWindow(
                start=datetime(2024, 6, 1, 0, 0, tzinfo=timezone.utc),
                end=datetime(2024, 6, 1, 6, 0, tzinfo=timezone.utc),
            )

            summary = acquire_environmental_data_for_investigation(
                investigation_id="inv-env-001",
                area_of_interest=aoi,
                time_window=window,
                scene_id="scene-s1-123",
                apply_framing=False,
                era5_provider=era5_p,
                cmems_provider=cmems_p,
                registry=registry,
            )

            # Verification
            self.assertEqual(summary.succeeded_providers, ["era5", "cmems"])
            self.assertEqual(summary.failed_providers, [])

            # ERA5 item
            era5_item = summary.items["era5"]
            self.assertEqual(era5_item.status, ProviderAcquisitionStatus.SUCCEEDED)
            self.assertIsNotNone(era5_item.asset)
            self.assertEqual(era5_item.asset.type, AssetType.ENVIRONMENT_WIND)
            self.assertIsInstance(era5_item.environment, WindField)
            self.assertEqual(era5_item.environment.kind, "wind")
            self.assertTrue(era5_item.validation.passed)

            # CMEMS item
            cmems_item = summary.items["cmems"]
            self.assertEqual(cmems_item.status, ProviderAcquisitionStatus.SUCCEEDED)
            self.assertIsNotNone(cmems_item.asset)
            self.assertEqual(cmems_item.asset.type, AssetType.ENVIRONMENT_CURRENT)
            self.assertIsInstance(cmems_item.environment, CurrentField)
            self.assertEqual(cmems_item.environment.kind, "current")
            self.assertTrue(cmems_item.validation.passed)

            # Check registered assets in AssetRegistry
            registered = registry.list_for_investigation("inv-env-001")
            self.assertEqual(len(registered), 2)
            asset_types = {a.type for a in registered}
            self.assertEqual(asset_types, {AssetType.ENVIRONMENT_WIND, AssetType.ENVIRONMENT_CURRENT})

    def test_partial_failure_era5_fails_cmems_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            settings = Settings(data_dir=tmp_path, cds_api_key="mock", cmems_username="u", cmems_password="p")

            mock_cds = MockCdsClient(should_fail=True)
            mock_cmems = MockCmemsClient()
            era5_p = Era5AcquisitionProvider(settings=settings, client=mock_cds)
            cmems_p = CmemsAcquisitionProvider(settings=settings, client=mock_cmems)
            registry = InMemoryAssetRegistry()

            aoi = BBoxAreaOfInterest(bbox=BoundingBox(west=2.0, south=51.0, east=3.0, north=52.0))
            window = TimeWindow(
                start=datetime(2024, 6, 1, 0, 0, tzinfo=timezone.utc),
                end=datetime(2024, 6, 1, 6, 0, tzinfo=timezone.utc),
            )

            summary = acquire_environmental_data_for_investigation(
                investigation_id="inv-partial-001",
                area_of_interest=aoi,
                time_window=window,
                apply_framing=False,
                era5_provider=era5_p,
                cmems_provider=cmems_p,
                registry=registry,
            )

            self.assertEqual(summary.succeeded_providers, ["cmems"])
            self.assertEqual(summary.failed_providers, ["era5"])
            self.assertEqual(summary.items["era5"].status, ProviderAcquisitionStatus.FAILED_ACQUISITION)
            self.assertEqual(summary.items["cmems"].status, ProviderAcquisitionStatus.SUCCEEDED)

            # Only CMEMS should be registered
            registered = registry.list_for_investigation("inv-partial-001")
            self.assertEqual(len(registered), 1)
            self.assertEqual(registered[0].type, AssetType.ENVIRONMENT_CURRENT)

    def test_partial_failure_cmems_fails_era5_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            settings = Settings(data_dir=tmp_path, cds_api_key="mock", cmems_username="u", cmems_password="p")

            mock_cds = MockCdsClient()
            mock_cmems = MockCmemsClient(should_fail=True)
            era5_p = Era5AcquisitionProvider(settings=settings, client=mock_cds)
            cmems_p = CmemsAcquisitionProvider(settings=settings, client=mock_cmems)
            registry = InMemoryAssetRegistry()

            aoi = BBoxAreaOfInterest(bbox=BoundingBox(west=2.0, south=51.0, east=3.0, north=52.0))
            window = TimeWindow(
                start=datetime(2024, 6, 1, 0, 0, tzinfo=timezone.utc),
                end=datetime(2024, 6, 1, 6, 0, tzinfo=timezone.utc),
            )

            summary = acquire_environmental_data_for_investigation(
                investigation_id="inv-partial-002",
                area_of_interest=aoi,
                time_window=window,
                apply_framing=False,
                era5_provider=era5_p,
                cmems_provider=cmems_p,
                registry=registry,
            )

            self.assertEqual(summary.succeeded_providers, ["era5"])
            self.assertEqual(summary.failed_providers, ["cmems"])
            self.assertEqual(summary.items["era5"].status, ProviderAcquisitionStatus.SUCCEEDED)
            self.assertEqual(summary.items["cmems"].status, ProviderAcquisitionStatus.FAILED_ACQUISITION)

            registered = registry.list_for_investigation("inv-partial-002")
            self.assertEqual(len(registered), 1)
            self.assertEqual(registered[0].type, AssetType.ENVIRONMENT_WIND)

    def test_validation_failure_prevents_asset_registration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            settings = Settings(data_dir=tmp_path, cds_api_key="mock", cmems_username="u", cmems_password="p")

            # Corrupted ERA5 download that fails A4.3 validation
            mock_cds = MockCdsClient(fail_validation=True)
            mock_cmems = MockCmemsClient()
            era5_p = Era5AcquisitionProvider(settings=settings, client=mock_cds)
            cmems_p = CmemsAcquisitionProvider(settings=settings, client=mock_cmems)
            registry = InMemoryAssetRegistry()

            aoi = BBoxAreaOfInterest(bbox=BoundingBox(west=2.0, south=51.0, east=3.0, north=52.0))
            window = TimeWindow(
                start=datetime(2024, 6, 1, 0, 0, tzinfo=timezone.utc),
                end=datetime(2024, 6, 1, 6, 0, tzinfo=timezone.utc),
            )

            summary = acquire_environmental_data_for_investigation(
                investigation_id="inv-val-fail",
                area_of_interest=aoi,
                time_window=window,
                apply_framing=False,
                era5_provider=era5_p,
                cmems_provider=cmems_p,
                registry=registry,
            )

            self.assertEqual(summary.items["era5"].status, ProviderAcquisitionStatus.FAILED_VALIDATION)
            self.assertFalse(summary.items["era5"].validation.passed)
            self.assertIsNone(summary.items["era5"].asset)

            # Ensure ERA5 was NOT registered in registry
            registered = registry.list_for_investigation("inv-val-fail")
            self.assertEqual(len(registered), 1)
            self.assertEqual(registered[0].type, AssetType.ENVIRONMENT_CURRENT)

    def test_acquire_for_scene_convenience(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            settings = Settings(data_dir=tmp_path, cds_api_key="mock", cmems_username="u", cmems_password="p")

            mock_cds = MockCdsClient()
            mock_cmems = MockCmemsClient()
            era5_p = Era5AcquisitionProvider(settings=settings, client=mock_cds)
            cmems_p = CmemsAcquisitionProvider(settings=settings, client=mock_cmems)
            registry = InMemoryAssetRegistry()

            scene = SatelliteScene(
                id="scene-sentinel1-test",
                investigation_id="inv-scene-env",
                asset_id="asset-s1",
                provider="sentinel1",
                sensor="SENTINEL-1 SAR",
                acquisition_time=datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc),
                footprint=PolygonAreaOfInterest(
                    kind="polygon",
                    coordinates=[[[2.0, 51.0], [3.0, 51.0], [3.0, 52.0], [2.0, 52.0], [2.0, 51.0]]],
                ),
            )

            summary = acquire_environmental_data_for_scene(
                scene=scene,
                spatial_buffer_degrees=0.5,
                lookback_hours=12.0,
                forward_hours=4.0,
                era5_provider=era5_p,
                cmems_provider=cmems_p,
                registry=registry,
            )

            self.assertEqual(summary.succeeded_providers, ["era5", "cmems"])
            # Framed bounding box should have 0.5 buffer around [2..3, 51..52] -> [1.5..3.5, 50.5..52.5]
            self.assertAlmostEqual(summary.area_of_interest.bbox.west, 1.5)
            self.assertAlmostEqual(summary.area_of_interest.bbox.east, 3.5)
            self.assertAlmostEqual(summary.area_of_interest.bbox.south, 50.5)
            self.assertAlmostEqual(summary.area_of_interest.bbox.north, 52.5)

            # Time window should be 12h before and 4h after 2024-06-01 10:00 -> [2024-05-31 22:00 .. 2024-06-01 14:00]
            self.assertEqual(summary.time_window.start, datetime(2024, 5, 31, 22, 0, tzinfo=timezone.utc))
            self.assertEqual(summary.time_window.end, datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc))


    def test_both_providers_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            settings = Settings(data_dir=tmp_path, cds_api_key="mock", cmems_username="u", cmems_password="p")

            mock_cds = MockCdsClient(should_fail=True)
            mock_cmems = MockCmemsClient(should_fail=True)
            era5_p = Era5AcquisitionProvider(settings=settings, client=mock_cds)
            cmems_p = CmemsAcquisitionProvider(settings=settings, client=mock_cmems)
            registry = InMemoryAssetRegistry()

            aoi = BBoxAreaOfInterest(bbox=BoundingBox(west=2.0, south=51.0, east=3.0, north=52.0))
            window = TimeWindow(
                start=datetime(2024, 6, 1, 0, 0, tzinfo=timezone.utc),
                end=datetime(2024, 6, 1, 6, 0, tzinfo=timezone.utc),
            )

            summary = acquire_environmental_data_for_investigation(
                investigation_id="inv-both-fail",
                area_of_interest=aoi,
                time_window=window,
                apply_framing=False,
                era5_provider=era5_p,
                cmems_provider=cmems_p,
                registry=registry,
            )

            self.assertEqual(summary.succeeded_providers, [])
            self.assertEqual(set(summary.failed_providers), {"era5", "cmems"})
            self.assertEqual(summary.items["era5"].status, ProviderAcquisitionStatus.FAILED_ACQUISITION)
            self.assertEqual(summary.items["cmems"].status, ProviderAcquisitionStatus.FAILED_ACQUISITION)
            self.assertEqual(len(registry.list_for_investigation("inv-both-fail")), 0)

    def test_request_construction_and_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            settings = Settings(data_dir=tmp_path, cds_api_key="mock", cmems_username="u", cmems_password="p")

            mock_cds = MockCdsClient()
            mock_cmems = MockCmemsClient()
            era5_p = Era5AcquisitionProvider(settings=settings, client=mock_cds)
            cmems_p = CmemsAcquisitionProvider(settings=settings, client=mock_cmems)
            registry = InMemoryAssetRegistry()

            aoi = BBoxAreaOfInterest(bbox=BoundingBox(west=2.0, south=51.0, east=3.0, north=52.0))
            window = TimeWindow(
                start=datetime(2024, 6, 1, 0, 0, tzinfo=timezone.utc),
                end=datetime(2024, 6, 1, 6, 0, tzinfo=timezone.utc),
            )

            summary = acquire_environmental_data_for_investigation(
                investigation_id="inv-prov",
                area_of_interest=aoi,
                time_window=window,
                scene_id="scene-parent-99",
                spatial_buffer_degrees=0.1,
                apply_framing=True,
                era5_provider=era5_p,
                cmems_provider=cmems_p,
                registry=registry,
            )

            # 24-hour lookback from midpoint 2024-06-01T03:00 gives framed start
            # 2024-05-31T03:00, spanning May and June. ERA5 provider correctly issues
            # one CDS request per calendar month, so >= 1 call is expected.
            self.assertGreaterEqual(len(mock_cds.calls), 1)
            cds_call = mock_cds.calls[0]
            self.assertEqual(cds_call["dataset"], "reanalysis-era5-single-levels")
            self.assertIn("area", cds_call["request"])

            self.assertEqual(len(mock_cmems.calls), 1)
            cmems_call = mock_cmems.calls[0]
            self.assertEqual(cmems_call["dataset_id"], "cmems_mod_glo_phy_my_0.083deg_P1D-m")
            self.assertEqual(cmems_call["minimum_depth"], 0.0)
            self.assertEqual(cmems_call["maximum_depth"], 1.0)

            # Check provenance links back to scene
            era5_asset = summary.items["era5"].asset
            self.assertIsNotNone(era5_asset)
            self.assertEqual(summary.items["era5"].environment.metadata["parent_scene_id"], "scene-parent-99")

            cmems_asset = summary.items["cmems"].asset
            self.assertIsNotNone(cmems_asset)
            self.assertEqual(summary.items["cmems"].environment.metadata["parent_scene_id"], "scene-parent-99")

    def test_deterministic_framing_and_behavior(self) -> None:
        anchor = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        aoi = BBoxAreaOfInterest(bbox=BoundingBox(west=2.0, south=40.0, east=4.0, north=42.0))

        b1 = derive_environmental_bbox(aoi, buffer_degrees=0.25)
        b2 = derive_environmental_bbox(aoi, buffer_degrees=0.25)
        self.assertEqual(b1, b2)

        w1 = derive_environmental_time_window(anchor, lookback_hours=24.0, forward_hours=6.0)
        w2 = derive_environmental_time_window(anchor, lookback_hours=24.0, forward_hours=6.0)
        self.assertEqual(w1, w2)


class EnvironmentalAcquisitionApiTests(unittest.TestCase):
    """Test the HTTP API route for environmental acquisition."""

    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_api_missing_context_fails_400(self) -> None:
        resp = self.client.post(
            "/api/v1/investigations/inv-api/environment/acquire",
            json={},
        )
        self.assertEqual(resp.status_code, 400)

    def test_api_unknown_scene_fails_404(self) -> None:
        resp = self.client.post(
            "/api/v1/investigations/inv-api/environment/acquire",
            json={"scene_id": "non_existent_scene"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_api_acquire_success_with_aoi_and_window(self) -> None:
        from unittest.mock import patch

        payload = {
            "area_of_interest": {
                "kind": "bbox",
                "bbox": {"west": 2.0, "south": 51.0, "east": 3.0, "north": 52.0},
            },
            "time_window": {
                "start": "2024-06-01T00:00:00Z",
                "end": "2024-06-01T06:00:00Z",
            },
            "providers": ["era5"],
            "spatial_buffer_degrees": 0.1,
            "lookback_hours": 6.0,
            "forward_hours": 2.0,
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            settings = Settings(data_dir=tmp_path, cds_api_key="mock")
            mock_cds = MockCdsClient()
            fake_provider = Era5AcquisitionProvider(settings=settings, client=mock_cds)

            with patch("app.services.environmental_acquisition.Era5AcquisitionProvider", return_value=fake_provider):
                resp = self.client.post(
                    "/api/v1/investigations/inv-api-env/environment/acquire",
                    json=payload,
                )

            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["investigation_id"], "inv-api-env")
            self.assertIn("era5", data["succeeded_providers"])
            self.assertIn("era5", data["items"])
            self.assertEqual(data["items"]["era5"]["status"], "succeeded")
            self.assertIsNotNone(data["items"]["era5"]["asset_id"])


if __name__ == "__main__":
    unittest.main()

