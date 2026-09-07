"""Unit and integration test suite for MARIS Stage B3 — Spill Detection & Geometry.

Uses controlled synthetic Sentinel-1 calibrated SAR GeoTIFF fixtures (sigma0 in dB)
built in-memory or in temporary directories to validate the detection algorithm,
polarization separation, geometry extraction, provenance, and domain models offline.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_origin

from app.acquisition.registry import InMemoryAssetRegistry
from app.main import app
from app.models.asset import Asset
from app.models.common import AssetType, BBoxAreaOfInterest, BoundingBox, PolygonAreaOfInterest, Provenance
from app.models.satellite import SatelliteScene, SpillDetection
from app.services.spill_detection import (
    AdaptiveThresholdSpillDetector,
    SpillDetectionError,
    compute_pixel_area_m2,
    detect_spills_from_sar_scene,
    extract_spill_geometries,
    select_polarization_band,
)


def _make_synthetic_b2_geotiff(
    path: Path,
    width: int = 60,
    height: int = 60,
    vv_bg: float = -10.0,
    vh_bg: float = -18.0,
    spill_coords: tuple[int, int, int, int] | None = None,  # (y1, y2, x1, x2)
    spill_vv_val: float = -20.0,  # 10 dB damping
    spill_vh_val: float = -26.0,
    include_vh: bool = True,
    include_nans: bool = False,
    origin_lon: float = 8.0,
    origin_lat: float = 43.0,
    pixel_res: float = 0.001,  # ~100m
) -> Path:
    """Create a calibrated Sentinel-1 sigma0 dB multi-band GeoTIFF fixture."""
    count = 2 if include_vh else 1
    transform = from_origin(origin_lon, origin_lat, pixel_res, pixel_res)

    vv_data = np.full((height, width), vv_bg, dtype=np.float32)
    if include_vh:
        vh_data = np.full((height, width), vh_bg, dtype=np.float32)

    if spill_coords is not None:
        y1, y2, x1, x2 = spill_coords
        vv_data[y1:y2, x1:x2] = spill_vv_val
        if include_vh:
            vh_data[y1:y2, x1:x2] = spill_vh_val

    if include_nans:
        vv_data[0:3, 0:3] = np.nan
        if include_vh:
            vh_data[0:3, 0:3] = np.nan

    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=count,
        dtype=np.float32,
        crs=CRS.from_epsg(4326),
        transform=transform,
        nodata=np.nan,
    ) as dst:
        dst.write(vv_data, 1)
        dst.set_band_description(1, "sigma0_db_VV")
        if include_vh:
            dst.write(vh_data, 2)
            dst.set_band_description(2, "sigma0_db_VH")

    return path


def _make_b2_asset(location: Path, scene_id: str = "scene-sar-001") -> Asset:
    retrieved = datetime.now(timezone.utc)
    return Asset(
        id="asset-b2-sar-test",
        investigation_id="inv-b3-test",
        type=AssetType.IMAGERY_PREVIEW,
        provider="sentinel1",
        source="sentinel1_preprocessing_service",
        location=str(location.resolve()),
        acquisition_time=retrieved,
        provenance=Provenance(
            product_id="S1A_IW_GRDH_1SDV_20181008_TEST",
            retrieved_at=retrieved,
            processing_level="calibrated_sigma0_db",
            notes="Stage B2 SAR calibrated GeoTIFF test fixture.",
            extra={"parent_scene_id": scene_id},
        ),
        metadata={
            "parent_scene_id": scene_id,
            "polarizations": ["VV", "VH"],
            "calibration": "sigma0_db",
        },
    )


def _make_scene(scene_id: str = "scene-sar-001", asset_id: str = "asset-b2-sar-test") -> SatelliteScene:
    return SatelliteScene(
        id=scene_id,
        investigation_id="inv-b3-test",
        asset_id=asset_id,
        provider="sentinel1",
        sensor="SENTINEL-1-A SAR",
        acquisition_time=datetime.now(timezone.utc),
        footprint=PolygonAreaOfInterest(
            kind="polygon",
            coordinates=[[[8.0, 42.5], [8.5, 42.5], [8.5, 43.0], [8.0, 43.0], [8.0, 42.5]]],
        ),
        metadata={"product_name": "S1A_IW_GRDH_TEST", "sensor_mode": "IW"},
    )


class AdaptiveSpillDetectorUnitTests(unittest.TestCase):
    """Unit tests for the AdaptiveThresholdSpillDetector algorithm."""

    def test_detector_parameter_validation(self) -> None:
        with self.assertRaises(ValueError):
            AdaptiveThresholdSpillDetector(damping_threshold_db=-1.0)
        with self.assertRaises(ValueError):
            AdaptiveThresholdSpillDetector(k_sigma=-0.5)
        with self.assertRaises(ValueError):
            AdaptiveThresholdSpillDetector(window_size_pixels=4)  # Even integer
        with self.assertRaises(ValueError):
            AdaptiveThresholdSpillDetector(border_margin_pixels=-1)

    def test_detector_identifies_dark_spot_statistically(self) -> None:
        detector = AdaptiveThresholdSpillDetector(
            damping_threshold_db=3.5,
            k_sigma=1.5,
            window_size_pixels=15,
            border_margin_pixels=0,
        )
        # 30x30 raster, clean sea = -10.0 dB, 6x6 slick = -18.0 dB (8 dB damping)
        raster = np.full((30, 30), -10.0, dtype=np.float32)
        raster[10:16, 10:16] = -18.0
        valid_mask = np.ones((30, 30), dtype=bool)

        result = detector.detect(
            raster=raster,
            valid_mask=valid_mask,
            polarization="VV",
            pixel_size_m=(10.0, 10.0),
        )

        self.assertEqual(result.mask.shape, (30, 30))
        self.assertEqual(result.probability.shape, (30, 30))
        # Slick pixels should be flagged as True
        self.assertTrue(np.any(result.mask[10:16, 10:16]))
        # Clean background pixels outside should be False
        self.assertFalse(result.mask[0, 0])
        self.assertFalse(result.mask[25, 25])
        # Probability on detected pixels should be high
        self.assertGreater(float(np.mean(result.probability[10:16, 10:16])), 0.7)
        # Probability on clean background should be 0.0
        self.assertEqual(float(result.probability[0, 0]), 0.0)

    def test_all_invalid_raster_returns_empty_cleanly(self) -> None:
        detector = AdaptiveThresholdSpillDetector()
        raster = np.full((20, 20), np.nan, dtype=np.float32)
        valid_mask = np.zeros((20, 20), dtype=bool)

        result = detector.detect(raster, valid_mask, "VV", (10.0, 10.0))
        self.assertEqual(int(np.sum(result.mask)), 0)
        self.assertEqual(result.metadata["candidate_pixel_count"], 0)


class GeometryExtractionUnitTests(unittest.TestCase):
    """Unit tests for connected-component analysis and vector geometry generation."""

    def test_empty_mask_returns_not_detected(self) -> None:
        mask = np.zeros((20, 20), dtype=bool)
        prob = np.zeros((20, 20), dtype=np.float32)
        raster = np.full((20, 20), -10.0, dtype=np.float32)
        transform = from_origin(8.0, 43.0, 0.001, 0.001)

        result = extract_spill_geometries(
            mask=mask,
            probability=prob,
            raster=raster,
            transform=transform,
            crs=CRS.from_epsg(4326),
            background_mean_db=-10.0,
        )

        self.assertFalse(result.detected)
        self.assertEqual(result.confidence, 0.0)
        self.assertEqual(result.total_area_m2, 0.0)
        self.assertEqual(result.spill_count, 0)
        self.assertEqual(result.geometry["type"], "GeometryCollection")
        self.assertIsNone(result.bbox)
        self.assertIsNone(result.centroid)

    def test_area_centroid_and_bbox_exactness(self) -> None:
        mask = np.zeros((40, 40), dtype=bool)
        # 10x10 patch from row 10 to 20, col 10 to 20
        mask[10:20, 10:20] = True
        prob = np.full((40, 40), 0.85, dtype=np.float32)
        raster = np.full((40, 40), -22.0, dtype=np.float32)
        transform = from_origin(8.0, 43.0, 0.001, 0.001)

        result = extract_spill_geometries(
            mask=mask,
            probability=prob,
            raster=raster,
            transform=transform,
            crs=CRS.from_epsg(4326),
            background_mean_db=-10.0,
            min_area_m2=100.0,
            min_pixels=5,
        )

        self.assertTrue(result.detected)
        self.assertEqual(result.spill_count, 1)
        self.assertIsNotNone(result.bbox)
        self.assertIsNotNone(result.centroid)

        west, south, east, north = result.bbox
        self.assertAlmostEqual(west, 8.010, places=4)
        self.assertAlmostEqual(east, 8.020, places=4)
        self.assertAlmostEqual(south, 42.980, places=4)
        self.assertAlmostEqual(north, 42.990, places=4)

        c_lon, c_lat = result.centroid
        self.assertAlmostEqual(c_lon, 8.015, places=4)
        self.assertAlmostEqual(c_lat, 42.985, places=4)

        # Expected pixel area ~9,043 m2 per pixel -> 100 pixels ~ 904,300 m2
        expected_pixel_area = compute_pixel_area_m2(transform, CRS.from_epsg(4326), center_lat=42.985)
        self.assertAlmostEqual(result.total_area_m2, 100 * expected_pixel_area, places=1)
        self.assertGreater(result.total_area_m2, 800_000.0)
        self.assertLess(result.total_area_m2, 1_000_000.0)

        # Check GeoJSON polygon coordinates
        geom = result.geometry
        self.assertEqual(geom["type"], "Polygon")
        ring = geom["coordinates"][0]
        self.assertEqual(ring[0], ring[-1])  # Closed linear ring

    def test_small_region_filtered_by_area(self) -> None:
        mask = np.zeros((30, 30), dtype=bool)
        # 2 isolated pixels
        mask[5, 5] = True
        mask[5, 6] = True
        prob = np.full((30, 30), 0.9, dtype=np.float32)
        raster = np.full((30, 30), -20.0, dtype=np.float32)
        transform = from_origin(8.0, 43.0, 0.001, 0.001)

        # min_pixels=5 should reject this 2-pixel feature
        result = extract_spill_geometries(
            mask=mask,
            probability=prob,
            raster=raster,
            transform=transform,
            crs=CRS.from_epsg(4326),
            background_mean_db=-10.0,
            min_pixels=5,
        )
        self.assertFalse(result.detected)
        self.assertEqual(result.spill_count, 0)



class SpillDetectionPipelineIntegrationTests(unittest.TestCase):
    """End-to-end integration tests for Stage B3 spill detection service."""

    def test_valid_b2_raster_detection_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tif_file = tmp_path / "sentinel1_sigma0_db.tif"
            # 60x60 raster with a 15x15 dark patch at (20..35, 20..35)
            _make_synthetic_b2_geotiff(
                path=tif_file,
                width=60,
                height=60,
                spill_coords=(20, 35, 20, 35),
                spill_vv_val=-22.0,  # 12 dB damping
                vv_bg=-10.0,
            )

            asset = _make_b2_asset(tif_file)
            scene = _make_scene()
            registry = InMemoryAssetRegistry()

            detector = AdaptiveThresholdSpillDetector(
                damping_threshold_db=3.5,
                k_sigma=1.5,
                window_size_pixels=25,
                border_margin_pixels=1,
            )

            spill_detection, derived_asset = detect_spills_from_sar_scene(
                investigation_id="inv-b3-test",
                scene=scene,
                sar_asset=asset,
                detector=detector,
                output_dir=tmp_path / "derived",
                registry=registry,
                min_area_m2=1000.0,
                min_pixels=5,
            )

            # 1. Detection domain object verification
            self.assertIsInstance(spill_detection, SpillDetection)
            self.assertTrue(spill_detection.detected)
            self.assertIsNotNone(spill_detection.confidence)
            self.assertGreaterEqual(spill_detection.confidence, 0.0)
            self.assertLessEqual(spill_detection.confidence, 1.0)
            self.assertGreater(spill_detection.area, 0.0)
            self.assertEqual(spill_detection.model_version, AdaptiveThresholdSpillDetector.MODEL_VERSION)
            self.assertEqual(spill_detection.scene_id, scene.id)
            self.assertEqual(spill_detection.investigation_id, "inv-b3-test")

            # 2. Geometry structure verification
            geom = spill_detection.geometry
            self.assertIn("type", geom)
            self.assertIn(geom["type"], ("Polygon", "MultiPolygon"))
            self.assertIn("coordinates", geom)

            # 3. Output files creation
            mask_path = tmp_path / "derived" / "spill_mask.tif"
            geojson_path = tmp_path / "derived" / "spill_geometry.geojson"
            self.assertTrue(mask_path.exists())
            self.assertTrue(geojson_path.exists())

            # 4. Mask GeoTIFF verification
            with rasterio.open(mask_path) as m_src:
                self.assertEqual(m_src.width, 60)
                self.assertEqual(m_src.height, 60)
                self.assertEqual(m_src.count, 2)  # Band 1 = Mask, Band 2 = Probability
                self.assertEqual(m_src.crs.to_epsg(), 4326)
                band1 = m_src.read(1)
                # Ensure detected patch contains positive values
                self.assertTrue(np.any(band1[22:33, 22:33] > 0))

            # 5. GeoJSON file verification
            geojson_content = json.loads(geojson_path.read_text(encoding="utf-8"))
            self.assertEqual(geojson_content["type"], "FeatureCollection")
            self.assertTrue(len(geojson_content["features"]) >= 1)
            first_feat = geojson_content["features"][0]
            self.assertIn("properties", first_feat)
            self.assertIn("area_m2", first_feat["properties"])
            self.assertIn("damping_contrast_db", first_feat["properties"])

            # 6. Derived Asset registration verification
            self.assertEqual(derived_asset.type, AssetType.SPILL_GEOMETRY)
            self.assertEqual(derived_asset.investigation_id, "inv-b3-test")
            self.assertEqual(derived_asset.location, str(geojson_path.resolve()))
            self.assertEqual(derived_asset.provenance.extra["parent_asset_id"], asset.id)
            self.assertEqual(derived_asset.provenance.extra["parent_scene_id"], scene.id)
            self.assertEqual(spill_detection.asset_id, derived_asset.id)

    def test_polarization_separation_vv_vs_vh(self) -> None:
        """Verify VV and VH are treated separately and can be independently selected."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tif_file = tmp_path / "dual_pol.tif"
            # Spill is dark in VV (-22 vs -10), but calm sea in VH (-18 vs -18, no damping)
            _make_synthetic_b2_geotiff(
                path=tif_file,
                width=50,
                height=50,
                spill_coords=(15, 30, 15, 30),
                vv_bg=-10.0,
                spill_vv_val=-22.0,  # 12 dB damping in VV
                vh_bg=-18.0,
                spill_vh_val=-18.0,  # 0 dB damping in VH
            )
            asset = _make_b2_asset(tif_file)
            scene = _make_scene()

            detector = AdaptiveThresholdSpillDetector(window_size_pixels=21, border_margin_pixels=1)

            # Test 1: Automatic selection selects VV by default -> DETECTED
            spill_vv, _ = detect_spills_from_sar_scene(
                "inv-b3-test", scene, asset, detector=detector, output_dir=tmp_path / "vv"
            )
            self.assertTrue(spill_vv.detected)
            self.assertEqual(spill_vv.metadata["polarization_used"], "VV")

            # Test 2: Explicit selection of VH -> NOT DETECTED (no damping in VH)
            spill_vh, _ = detect_spills_from_sar_scene(
                "inv-b3-test", scene, asset, detector=detector, polarization="VH", output_dir=tmp_path / "vh"
            )
            self.assertFalse(spill_vh.detected)
            self.assertEqual(spill_vh.metadata["polarization_used"], "VH")

    def test_deterministic_detection(self) -> None:
        """Verify that identical input rasters produce identical detections bit-for-bit."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tif_file = tmp_path / "det_test.tif"
            _make_synthetic_b2_geotiff(path=tif_file, spill_coords=(20, 35, 20, 35))
            asset = _make_b2_asset(tif_file)
            scene = _make_scene()

            detector = AdaptiveThresholdSpillDetector(window_size_pixels=21)

            res1, _ = detect_spills_from_sar_scene(
                "inv-b3-test", scene, asset, detector=detector, output_dir=tmp_path / "run1"
            )
            res2, _ = detect_spills_from_sar_scene(
                "inv-b3-test", scene, asset, detector=detector, output_dir=tmp_path / "run2"
            )

            self.assertEqual(res1.detected, res2.detected)
            self.assertEqual(res1.confidence, res2.confidence)
            self.assertEqual(res1.area, res2.area)
            self.assertEqual(res1.geometry, res2.geometry)

    def test_no_detection_on_clean_ocean(self) -> None:
        """Verify that clean uniform sea clutter yields detected=False and area=0."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tif_file = tmp_path / "clean_sea.tif"
            _make_synthetic_b2_geotiff(path=tif_file, spill_coords=None)  # No spill
            asset = _make_b2_asset(tif_file)
            scene = _make_scene()

            res, _ = detect_spills_from_sar_scene(
                "inv-b3-test", scene, asset, output_dir=tmp_path / "derived"
            )
            self.assertFalse(res.detected)
            self.assertEqual(res.confidence, 0.0)
            self.assertEqual(res.area, 0.0)
            self.assertEqual(res.metadata["spill_count"], 0)

    def test_multiple_connected_spills_extraction(self) -> None:
        """Verify multiple separate slicks are extracted as individual regions and MultiPolygon."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tif_file = tmp_path / "multi_spill.tif"
            # 80x80 raster with two distinct dark patches
            transform = from_origin(8.0, 43.0, 0.001, 0.001)
            vv_data = np.full((80, 80), -10.0, dtype=np.float32)
            # Slick 1 at top-left
            vv_data[15:25, 15:25] = -22.0
            # Slick 2 at bottom-right
            vv_data[50:60, 50:60] = -22.0

            with rasterio.open(
                tif_file, "w", driver="GTiff", height=80, width=80, count=1,
                dtype=np.float32, crs="EPSG:4326", transform=transform
            ) as dst:
                dst.write(vv_data, 1)
                dst.set_band_description(1, "sigma0_db_VV")

            asset = _make_b2_asset(tif_file)
            scene = _make_scene()

            detector = AdaptiveThresholdSpillDetector(window_size_pixels=21, border_margin_pixels=1)

            res, _ = detect_spills_from_sar_scene(
                "inv-b3-test", scene, asset, detector=detector, output_dir=tmp_path / "derived",
                min_area_m2=500.0, min_pixels=5
            )

            self.assertTrue(res.detected)
            self.assertEqual(res.metadata["spill_count"], 2)
            self.assertEqual(len(res.metadata["regions"]), 2)
            self.assertEqual(res.geometry["type"], "MultiPolygon")

    def test_invalid_and_nan_pixels_handling(self) -> None:
        """Verify NaNs, non-finite values, and sensor nodata are completely ignored."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tif_file = tmp_path / "nan_test.tif"
            _make_synthetic_b2_geotiff(
                path=tif_file,
                spill_coords=(20, 35, 20, 35),
                include_nans=True,
            )
            asset = _make_b2_asset(tif_file)
            scene = _make_scene()

            detector = AdaptiveThresholdSpillDetector(window_size_pixels=21)
            res, _ = detect_spills_from_sar_scene(
                "inv-b3-test", scene, asset, detector=detector, output_dir=tmp_path / "derived"
            )

            self.assertTrue(res.detected)
            # Ensure none of the NaN pixels at (0..3, 0..3) were flagged
            with rasterio.open(tmp_path / "derived" / "spill_mask.tif") as m_src:
                mask = m_src.read(1)
                self.assertEqual(float(np.sum(mask[0:3, 0:3])), 0.0)

    def test_missing_or_corrupt_raster_fails_closed(self) -> None:
        asset = _make_b2_asset(Path("non_existent_file.tif"))
        scene = _make_scene()

        with self.assertRaises(SpillDetectionError) as ctx:
            detect_spills_from_sar_scene("inv-b3-test", scene, asset)
        self.assertIn("does not exist", str(ctx.exception))

    def test_source_b2_raster_hash_unchanged(self) -> None:
        """Verify source GeoTIFF is opened strictly read-only and remains 100% byte-for-byte unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tif_file = tmp_path / "readonly_check.tif"
            _make_synthetic_b2_geotiff(path=tif_file, spill_coords=(20, 35, 20, 35))

            hash_before = hashlib.sha256(tif_file.read_bytes()).hexdigest()

            asset = _make_b2_asset(tif_file)
            scene = _make_scene()

            detect_spills_from_sar_scene("inv-b3-test", scene, asset, output_dir=tmp_path / "derived")

            hash_after = hashlib.sha256(tif_file.read_bytes()).hexdigest()
            self.assertEqual(hash_before, hash_after)

    def test_deterministic_default_output_path(self) -> None:
        """Verify output files are written to {MARIS_DATA_DIR}/derived/{inv}/{scene}/."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tif_file = tmp_path / "path_test.tif"
            _make_synthetic_b2_geotiff(path=tif_file, spill_coords=(20, 35, 20, 35))
            asset = _make_b2_asset(tif_file)
            scene = _make_scene()

            # output_dir=None triggers default path
            _, derived_asset = detect_spills_from_sar_scene("inv-b3-det-path", scene, asset)
            loc = Path(derived_asset.location)

            self.assertIn("derived", loc.parts)
            self.assertIn("inv-b3-det-path", loc.parts)
            self.assertIn("sar", loc.parts)
            self.assertIn(scene.id, loc.parts)
            self.assertEqual(loc.name, "spill_geometry.geojson")


class SpillDetectApiEndpointTests(unittest.TestCase):
    """Integration tests for the HTTP API route."""

    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_api_detect_endpoint_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tif_file = tmp_path / "api_test.tif"
            _make_synthetic_b2_geotiff(path=tif_file, spill_coords=(20, 35, 20, 35))

            payload = {
                "sar_asset_path": str(tif_file.resolve()),
                "damping_threshold_db": 3.5,
                "k_sigma": 1.5,
                "min_area_m2": 1000.0,
            }

            resp = self.client.post(
                "/api/v1/investigations/inv-api-test/scenes/scene-api-001/spill-detect",
                json=payload,
            )

            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("spill_detection", data)
            self.assertIn("asset_id", data)
            self.assertTrue(data["spill_detection"]["detected"])
            self.assertGreater(data["spill_detection"]["area"], 0)

    def test_api_detect_missing_asset_fails_404(self) -> None:
        payload = {
            "sar_asset_path": "non_existent_sar_raster.tif",
        }
        resp = self.client.post(
            "/api/v1/investigations/inv-api-test/scenes/scene-api-001/spill-detect",
            json=payload,
        )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
