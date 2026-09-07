"""Offline test suite for Stage B1 — Real Sentinel-1 Scene Ingestion."""

import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from fastapi.testclient import TestClient

from app.acquisition.registry import InMemoryAssetRegistry
from app.main import app
from app.models.common import PolygonAreaOfInterest
from app.models.satellite import SatelliteScene
from app.services.sentinel1_ingestion import (
    Sentinel1IngestionError,
    create_scene_from_asset_and_validation,
    gml_coords_to_polygon,
    ingest_sentinel1_artifact,
)

_MANIFEST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<xfdu:XFDU xmlns:xfdu="urn:ccsds:schema:xfdu:1"
  xmlns:safe="http://www.esa.int/safe/sentinel/1.1"
  xmlns:s1="http://www.esa.int/safe/sentinel-1.0"
  xmlns:s1sarl1="http://www.esa.int/safe/sentinel-1.0/sentinel-1/sar/level-1"
  xmlns:gml="http://www.opengis.net/gml">
  <metadataSection>
    <metadataObject ID="generalProductInformation"><metadataWrap><xmlData>
      <s1sarl1:standAloneProductInformation>
        <s1sarl1:productType>{product_type}</s1sarl1:productType>
        <s1sarl1:polarisationChannels>{polarisation}</s1sarl1:polarisationChannels>
      </s1sarl1:standAloneProductInformation>
    </xmlData></metadataWrap></metadataObject>
    <metadataObject ID="acquisitionPeriod"><metadataWrap><xmlData>
      <safe:acquisitionPeriod>
        <safe:startTime>{start_time}</safe:startTime>
        <safe:stopTime>{stop_time}</safe:stopTime>
      </safe:acquisitionPeriod>
    </xmlData></metadataWrap></metadataObject>
    <metadataObject ID="platform"><metadataWrap><xmlData>
      <safe:platform>
        <safe:familyName>{platform_family}</safe:familyName>
        <safe:number>{platform_number}</safe:number>
        <safe:instrument>
          <safe:familyName abbreviation="{instrument_abbrev}">{instrument_family}</safe:familyName>
          <safe:extension>
            <s1:instrumentMode><s1:mode>{sensor_mode}</s1:mode></s1:instrumentMode>
          </safe:extension>
        </safe:instrument>
      </safe:platform>
    </xmlData></metadataWrap></metadataObject>
    <metadataObject ID="measurementOrbitReference"><metadataWrap><xmlData>
      <safe:orbitReference>
        <safe:orbitNumber type="start">{orbit_number}</safe:orbitNumber>
      </safe:orbitReference>
    </xmlData></metadataWrap></metadataObject>
    <metadataObject ID="measurementFrameSet"><metadataWrap><xmlData>
      <safe:frameSet><safe:frame>
        <safe:footPrint srsName="http://www.opengis.net/gml/srs/epsg.xml#4326">
          <gml:coordinates>{footprint}</gml:coordinates>
        </safe:footPrint>
      </safe:frame></safe:frameSet>
    </xmlData></metadataWrap></metadataObject>
  </metadataSection>
</xfdu:XFDU>"""

_DEFAULT_FIELDS = dict(
    product_type="GRD",
    polarisation="VV VH",
    start_time="2018-10-08T06:30:00.000000",
    stop_time="2018-10-08T06:30:25.000000",
    platform_family="SENTINEL-1",
    platform_number="A",
    instrument_abbrev="SAR",
    instrument_family="C-SAR",
    sensor_mode="IW",
    orbit_number="23855",
    footprint="41.5,8.2 41.8,9.1 42.5,8.9 42.2,8.0 41.5,8.2",
)

_SAFE_DIR = "S1A_IW_GRDH_1SDV_20181008T063000_20181008T063025_023855_029B3D_1234.SAFE"


def _make_zip_bytes(
    safe_dir: str = _SAFE_DIR,
    include_manifest: bool = True,
    **overrides: str,
) -> bytes:
    buf = io.BytesIO()
    fields = {**_DEFAULT_FIELDS, **overrides}
    xml = _MANIFEST_TEMPLATE.format(**fields)
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(f"{safe_dir}/", "")
        if include_manifest:
            zf.writestr(f"{safe_dir}/manifest.safe", xml)
    return buf.getvalue()


class Sentinel1IngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_gml_coords_to_polygon_valid(self) -> None:
        gml = "41.5,8.2 41.8,9.1 42.5,8.9 42.2,8.0 41.5,8.2"
        polygon = gml_coords_to_polygon(gml)
        self.assertIsInstance(polygon, PolygonAreaOfInterest)
        self.assertEqual(polygon.kind, "polygon")
        ring = polygon.coordinates[0]
        # Coordinates in PolygonAreaOfInterest are [lon, lat]
        self.assertEqual(ring[0], [8.2, 41.5])
        self.assertEqual(ring[1], [9.1, 41.8])
        self.assertEqual(ring[2], [8.9, 42.5])
        self.assertEqual(ring[3], [8.0, 42.2])
        self.assertEqual(ring[4], [8.2, 41.5])  # Closed ring

    def test_gml_coords_to_polygon_auto_close(self) -> None:
        gml = "41.5,8.2 41.8,9.1 42.5,8.9 42.2,8.0"  # 4 points, unclosed
        polygon = gml_coords_to_polygon(gml)
        ring = polygon.coordinates[0]
        self.assertEqual(len(ring), 5)
        self.assertEqual(ring[0], ring[-1])

    def test_gml_coords_to_polygon_malformed(self) -> None:
        with self.assertRaises(Sentinel1IngestionError):
            gml_coords_to_polygon("")
        with self.assertRaises(Sentinel1IngestionError):
            gml_coords_to_polygon("not-a-coord")
        with self.assertRaises(Sentinel1IngestionError):
            gml_coords_to_polygon("95.0,8.2 41.8,9.1 42.5,8.9 95.0,8.2")  # Lat out of range
        with self.assertRaises(Sentinel1IngestionError):
            gml_coords_to_polygon("41.5,200.0 41.8,9.1 42.5,8.9 41.5,200.0")  # Lon out of range

    def test_successful_ingestion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "s1_product.zip"
            zip_bytes = _make_zip_bytes()
            file_path.write_bytes(zip_bytes)

            registry = InMemoryAssetRegistry()
            investigation_id = "inv-b1-test"

            scene, val_result, asset = ingest_sentinel1_artifact(
                investigation_id=investigation_id,
                artifact_path=file_path,
                registry=registry,
            )

            # 1. SatelliteScene created
            self.assertIsInstance(scene, SatelliteScene)
            self.assertEqual(scene.investigation_id, investigation_id)

            # 2. Asset registered in registry
            self.assertEqual(scene.asset_id, asset.id)
            registered_asset = registry.get(asset.id)
            self.assertEqual(registered_asset.id, asset.id)
            self.assertEqual(registered_asset.investigation_id, investigation_id)

            # 3. Validation succeeded
            self.assertTrue(val_result.passed)

            # 4. Acquisition time comes from sensing_start
            self.assertEqual(scene.acquisition_time.year, 2018)
            self.assertEqual(scene.acquisition_time.month, 10)
            self.assertEqual(scene.acquisition_time.day, 8)

            # 5. Sensor mapping
            self.assertEqual(scene.sensor, "SENTINEL-1-A C-SAR")

            # 6. Metadata preservation
            self.assertEqual(scene.metadata.get("product_type"), "GRD")
            self.assertEqual(scene.metadata.get("sensor_mode"), "IW")
            self.assertEqual(scene.metadata.get("polarisation"), "VV VH")
            self.assertEqual(scene.metadata.get("orbit_number"), "23855")

            # 7. Footprint longitude/latitude order
            self.assertEqual(scene.footprint.coordinates[0][0], [8.2, 41.5])

    def test_corrupt_zip_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "corrupt.zip"
            file_path.write_bytes(b"invalid zip file bytes")

            registry = InMemoryAssetRegistry()
            with self.assertRaises(Sentinel1IngestionError):
                ingest_sentinel1_artifact("inv-1", file_path, registry=registry)

            # Confirm no asset registered
            self.assertEqual(len(registry.list_for_investigation("inv-1")), 0)

    def test_missing_manifest_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "no_manifest.zip"
            file_path.write_bytes(_make_zip_bytes(include_manifest=False))

            registry = InMemoryAssetRegistry()
            with self.assertRaises(Sentinel1IngestionError):
                ingest_sentinel1_artifact("inv-1", file_path, registry=registry)

            self.assertEqual(len(registry.list_for_investigation("inv-1")), 0)

    def test_invalid_validation_result_prevents_scene_and_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Platform SENTINEL-2 causes validation ERROR in Sentinel1Validator
            file_path = Path(tmp) / "invalid_platform.zip"
            file_path.write_bytes(_make_zip_bytes(platform_family="SENTINEL-2"))

            registry = InMemoryAssetRegistry()
            with self.assertRaises(Sentinel1IngestionError) as ctx:
                ingest_sentinel1_artifact("inv-1", file_path, registry=registry)

            self.assertIn("validation failed", str(ctx.exception).lower())
            self.assertEqual(len(registry.list_for_investigation("inv-1")), 0)

    def test_source_zip_unchanged_after_ingestion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_bytes = _make_zip_bytes()
            file_path = Path(tmp) / "source.zip"
            file_path.write_bytes(zip_bytes)

            mtime_before = file_path.stat().st_mtime
            ingest_sentinel1_artifact("inv-1", file_path)

            self.assertEqual(file_path.read_bytes(), zip_bytes)
            self.assertEqual(file_path.stat().st_mtime, mtime_before)

    def test_api_valid_ingestion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "api_test.zip"
            file_path.write_bytes(_make_zip_bytes())

            url = f"/api/v1/investigations/inv-api-123/scenes/sentinel1/ingest"
            response = self.client.post(url, json={"artifact_path": str(file_path)})

            self.assertEqual(response.status_code, 200)
            data = response.json()

            self.assertIn("scene", data)
            self.assertIn("validation", data)

            scene = data["scene"]
            self.assertEqual(scene["investigation_id"], "inv-api-123")
            self.assertEqual(scene["provider"], "sentinel1")
            self.assertEqual(scene["footprint"]["kind"], "polygon")
            self.assertTrue(data["validation"]["passed"])

    def test_api_invalid_artifact_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "corrupt_api.zip"
            file_path.write_bytes(b"corrupt header")

            url = "/api/v1/investigations/inv-api-123/scenes/sentinel1/ingest"
            response = self.client.post(url, json={"artifact_path": str(file_path)})

            self.assertEqual(response.status_code, 422)
            self.assertIn("detail", response.json())

    def test_api_missing_artifact_response(self) -> None:
        url = "/api/v1/investigations/inv-api-123/scenes/sentinel1/ingest"
        response = self.client.post(url, json={"artifact_path": "/nonexistent/path/product.zip"})

        self.assertEqual(response.status_code, 404)
        self.assertIn("detail", response.json())


if __name__ == "__main__":
    unittest.main()
