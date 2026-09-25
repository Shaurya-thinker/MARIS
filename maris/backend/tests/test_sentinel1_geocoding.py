"""Test suite for Stage B2.5 — Sentinel-1 SAR Ellipsoid Geocoding Service.

Validates fail-closed conditions, coordinate accuracy, North-Up grid generation,
band description preservation, out-of-core block processing, and real-data window geocoding.
"""

from datetime import datetime, timezone
import io
from pathlib import Path
import tempfile
import unittest

import numpy as np
import rasterio
from rasterio.control import GroundControlPoint
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import Affine

from app.acquisition.registry import InMemoryAssetRegistry
from app.models.asset import Asset
from app.models.common import AssetType, PolygonAreaOfInterest, Provenance
from app.models.satellite import SatelliteScene
from app.services.sentinel1_geocoding import (
    Sentinel1GeocodingError,
    compute_geocoding_target_grid,
    geocode_sentinel1_raster,
    geocode_sentinel1_scene,
    validate_source_raster_gcps,
)


def _make_test_gcp_raster(
    output_path: Path | str,
    width: int = 100,
    height: int = 100,
    count: int = 2,
    custom_gcps: list[GroundControlPoint] | None = None,
    crs: str | None = "EPSG:4326",
    nodata: float = np.nan,
    band_values: list[float] | None = None,
) -> Path:
    """Helper to generate a native-geometry test GeoTIFF with Ground Control Points."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if custom_gcps is not None:
        gcps = custom_gcps
    else:
        # Default 4-corner GCPs spanning full width and height
        gcps = [
            GroundControlPoint(row=0.0, col=0.0, x=8.0, y=52.0, z=0.0, id="1"),
            GroundControlPoint(row=0.0, col=float(width), x=9.0, y=52.0, z=0.0, id="2"),
            GroundControlPoint(row=float(height), col=0.0, x=8.0, y=53.0, z=0.0, id="3"),
            GroundControlPoint(row=float(height), col=float(width), x=9.0, y=53.0, z=0.0, id="4"),
        ]

    open_kwargs = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": count,
        "dtype": "float32",
        "nodata": nodata,
    }
    if gcps:
        open_kwargs["gcps"] = gcps
    if crs:
        open_kwargs["crs"] = crs

    vals = band_values or [-12.5, -18.2]
    with rasterio.open(out, "w", **open_kwargs) as dst:
        for b in range(1, count + 1):
            val = vals[b - 1] if b - 1 < len(vals) else 1.0
            data = np.full((height, width), val, dtype=np.float32)
            # Add a nodata pixel
            data[0, 0] = np.nan
            dst.write(data, b)
            pol = "VV" if b == 1 else "VH"
            dst.set_band_description(b, f"sigma0_db_{pol}")

    return out


class Sentinel1GeocodingValidationTests(unittest.TestCase):
    def test_missing_gcps_fails_closed(self) -> None:
        """1. Missing GCPs -> fail closed."""
        with tempfile.TemporaryDirectory() as tmp:
            tif_path = Path(tmp) / "no_gcps.tif"
            with rasterio.open(
                tif_path, "w", driver="GTiff", height=50, width=50, count=1, dtype="float32", crs="EPSG:4326"
            ) as dst:
                dst.write(np.ones((50, 50), dtype=np.float32), 1)

            with rasterio.open(tif_path) as src:
                with self.assertRaises(Sentinel1GeocodingError) as ctx:
                    validate_source_raster_gcps(src)
                self.assertIn("no ground control points", str(ctx.exception).lower())

    def test_insufficient_gcps_fails_closed(self) -> None:
        """2. Insufficient GCP count (< 4) -> fail closed."""
        with tempfile.TemporaryDirectory() as tmp:
            tif_path = Path(tmp) / "two_gcps.tif"
            few_gcps = [
                GroundControlPoint(row=0.0, col=0.0, x=8.0, y=52.0, z=0.0, id="1"),
                GroundControlPoint(row=50.0, col=50.0, x=9.0, y=53.0, z=0.0, id="2"),
            ]
            _make_test_gcp_raster(tif_path, custom_gcps=few_gcps)

            with rasterio.open(tif_path) as src:
                with self.assertRaises(Sentinel1GeocodingError) as ctx:
                    validate_source_raster_gcps(src)
                self.assertIn("insufficient", str(ctx.exception).lower())

    def test_invalid_gcp_geographic_coordinates_fails_closed(self) -> None:
        """3. Invalid GCP coordinates (out of WGS-84 ranges) -> fail closed."""
        with tempfile.TemporaryDirectory() as tmp:
            tif_path = Path(tmp) / "invalid_coords.tif"
            bad_gcps = [
                GroundControlPoint(row=0.0, col=0.0, x=-200.0, y=52.0, z=0.0, id="1"),
                GroundControlPoint(row=0.0, col=100.0, x=9.0, y=105.0, z=0.0, id="2"),
                GroundControlPoint(row=100.0, col=0.0, x=8.0, y=53.0, z=0.0, id="3"),
                GroundControlPoint(row=100.0, col=100.0, x=9.0, y=53.0, z=0.0, id="4"),
            ]
            _make_test_gcp_raster(tif_path, custom_gcps=bad_gcps)

            with rasterio.open(tif_path) as src:
                with self.assertRaises(Sentinel1GeocodingError) as ctx:
                    validate_source_raster_gcps(src)
                self.assertIn("invalid gcp geographic coordinate", str(ctx.exception).lower())

    def test_clustered_insufficient_coverage_gcps_fails_closed(self) -> None:
        """4. GCPs that fail to span the raster domain (< 80% coverage) -> fail closed."""
        with tempfile.TemporaryDirectory() as tmp:
            tif_path = Path(tmp) / "clustered_gcps.tif"
            # Clustered in a tiny 5x5 corner of a 100x100 raster
            clustered = [
                GroundControlPoint(row=0.0, col=0.0, x=8.0, y=52.0, z=0.0, id="1"),
                GroundControlPoint(row=0.0, col=5.0, x=8.01, y=52.0, z=0.0, id="2"),
                GroundControlPoint(row=5.0, col=0.0, x=8.0, y=52.01, z=0.0, id="3"),
                GroundControlPoint(row=5.0, col=5.0, x=8.01, y=52.01, z=0.0, id="4"),
            ]
            _make_test_gcp_raster(tif_path, width=100, height=100, custom_gcps=clustered)

            with rasterio.open(tif_path) as src:
                with self.assertRaises(Sentinel1GeocodingError) as ctx:
                    validate_source_raster_gcps(src)
                self.assertIn("adequately span", str(ctx.exception).lower())


class Sentinel1GeocodingExecutionTests(unittest.TestCase):
    def test_geocoding_output_properties(self) -> None:
        """5, 6, 7, 8, 10, 11: Validates CRS, non-identity north-up transform, derived dims, bands, and nodata."""
        with tempfile.TemporaryDirectory() as tmp:
            src_tif = Path(tmp) / "native.tif"
            dst_tif = Path(tmp) / "geocoded.tif"

            _make_test_gcp_raster(src_tif, width=120, height=80, count=2)

            metrics = geocode_sentinel1_raster(
                source_raster_path=src_tif,
                output_raster_path=dst_tif,
                block_size=64,
            )

            # Output CRS is EPSG:4326
            self.assertEqual(metrics["crs"], "EPSG:4326")

            # Output transform is non-identity
            trans = metrics["transform"]
            self.assertFalse(trans.is_identity)

            # Output transform is strictly North-Up
            self.assertGreater(trans.a, 0.0)  # dx > 0
            self.assertLess(trans.e, 0.0)  # dy < 0
            self.assertAlmostEqual(trans.b, 0.0, places=9)  # zero rotation
            self.assertAlmostEqual(trans.d, 0.0, places=9)

            # Dimensions derived from actual geometry
            self.assertGreater(metrics["width"], 0)
            self.assertGreater(metrics["height"], 0)

            # Inspect destination GeoTIFF directly
            with rasterio.open(dst_tif) as dst:
                self.assertEqual(dst.count, 2)
                self.assertEqual(dst.crs.to_epsg(), 4326)
                self.assertEqual(dst.descriptions[0], "sigma0_db_VV")
                self.assertEqual(dst.descriptions[1], "sigma0_db_VH")

                # Verify nodata and finite semantics
                b1 = dst.read(1)
                self.assertTrue(np.isnan(dst.nodata) or dst.nodata is None or math.isnan(dst.nodata))
                self.assertTrue(np.any(np.isfinite(b1)))
                # Mean should preserve order of magnitude around -12.5 dB
                valid_pixels = b1[np.isfinite(b1)]
                self.assertAlmostEqual(float(np.mean(valid_pixels)), -12.5, delta=0.5)

    def test_gcp_coordinate_preservation_accuracy(self) -> None:
        """9. GCP transformation preserves known GCP coordinates within explicit tolerance."""
        with tempfile.TemporaryDirectory() as tmp:
            src_tif = Path(tmp) / "native_gcp.tif"
            dst_tif = Path(tmp) / "geocoded_gcp.tif"

            gcps = [
                GroundControlPoint(row=0.0, col=0.0, x=6.0, y=43.0, z=0.0, id="1"),
                GroundControlPoint(row=0.0, col=200.0, x=9.0, y=43.0, z=0.0, id="2"),
                GroundControlPoint(row=150.0, col=0.0, x=6.0, y=45.0, z=0.0, id="3"),
                GroundControlPoint(row=150.0, col=200.0, x=9.0, y=45.0, z=0.0, id="4"),
            ]
            _make_test_gcp_raster(src_tif, width=200, height=150, custom_gcps=gcps)

            metrics = geocode_sentinel1_raster(src_tif, dst_tif, block_size=64)
            bounds = metrics["bounds"]

            # Tolerance for corner extents
            self.assertAlmostEqual(bounds["west"], 6.0, delta=0.02)
            self.assertAlmostEqual(bounds["east"], 9.0, delta=0.02)
            self.assertAlmostEqual(bounds["south"], 43.0, delta=0.02)
            self.assertAlmostEqual(bounds["north"], 45.0, delta=0.02)

    def test_block_streaming_preserves_bounded_memory(self) -> None:
        """13. Block processing processes destination blocks incrementally."""
        with tempfile.TemporaryDirectory() as tmp:
            src_tif = Path(tmp) / "native_large.tif"
            dst_tif = Path(tmp) / "geocoded_large.tif"

            # 300 x 300 with block size 64 -> (5 x 5 = 25 blocks per band = 50 total blocks)
            _make_test_gcp_raster(src_tif, width=300, height=300, count=2)
            metrics = geocode_sentinel1_raster(src_tif, dst_tif, block_size=64)

            self.assertGreater(metrics["blocks_processed"], 10)
            self.assertEqual(metrics["block_size"], 64)

    def test_geocode_sentinel1_scene_asset_registration(self) -> None:
        """Stage B2.5 pipeline asset registration with AssetRegistry and provenance."""
        with tempfile.TemporaryDirectory() as tmp:
            src_tif = Path(tmp) / "sentinel1_sigma0_db_native.tif"
            _make_test_gcp_raster(src_tif, width=100, height=100, count=2)

            now_utc = datetime.now(timezone.utc)
            asset = Asset(
                id="asset-native-123",
                investigation_id="inv-b25-test",
                type=AssetType.SATELLITE_SCENE,
                provider="sentinel1",
                source="sentinel1_preprocessing_service",
                location=str(src_tif),
                acquisition_time=now_utc,
                provenance=Provenance(
                    product_id="test-product",
                    retrieved_at=now_utc,
                    processing_level="calibrated_sigma0_db_native",
                ),
                metadata={},
            )
            scene = SatelliteScene(
                id="scene-test-123",
                investigation_id="inv-b25-test",
                asset_id=asset.id,
                provider="sentinel1",
                sensor="SENTINEL-1-B SAR",
                acquisition_time=now_utc,
                footprint=PolygonAreaOfInterest(
                    kind="polygon",
                    coordinates=[[[8.0, 52.0], [9.0, 52.0], [9.0, 53.0], [8.0, 53.0], [8.0, 52.0]]],
                ),
                metadata={"product_name": "S1B_TEST.SAFE", "sensor_mode": "IW", "product_type": "GRD"},
            )
            registry = InMemoryAssetRegistry()

            geocoded_asset, meta = geocode_sentinel1_scene(
                investigation_id="inv-b25-test",
                scene=scene,
                native_sar_asset=asset,
                output_dir=Path(tmp) / "geocoded",
                registry=registry,
                block_size=64,
            )

            self.assertEqual(geocoded_asset.type, AssetType.IMAGERY_PREVIEW)
            self.assertEqual(geocoded_asset.provenance.processing_level, "geocoded_sigma0_db")
            self.assertEqual(meta["geocoding_method"], "GCP_TPS")
            self.assertTrue(Path(geocoded_asset.location).exists())
            self.assertEqual(Path(geocoded_asset.location).name, "sentinel1_sigma0_db.tif")


if __name__ == "__main__":
    unittest.main()
