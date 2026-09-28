"""Offline test suite for Stage B2 — SAR Preprocessing & Scene Preparation.

Uses controlled synthetic Sentinel-1 GRD ZIP test fixtures built in-memory.
NOTE: These fixtures are synthetic test structures used for offline logic
and mathematical validation. No real Sentinel-1 ZIP artifact is present in the repository.
"""

from datetime import datetime, timezone
import hashlib
import io
from pathlib import Path
import tempfile
from typing import Any
import unittest
import zipfile

import numpy as np
import rasterio
from rasterio.control import GroundControlPoint
from rasterio.transform import Affine, from_origin

from app.acquisition.registry import InMemoryAssetRegistry
from app.models.asset import Asset
from app.models.common import AssetType, PolygonAreaOfInterest, Provenance
from app.models.satellite import SatelliteScene
from app.services.sentinel1_preprocessing import (
    Sentinel1PreprocessingError,
    build_calibration_lut_1d,
    compute_sigma0_db,
    parse_calibration_xml,
    preprocess_sentinel1_scene,
)

_SAFE_DIR = "S1A_IW_GRDH_1SDV_20181008T063000_20181008T063025_023855_029B3D_1234.SAFE"

_CALIBRATION_XML_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<calibration>
  <adsHeader>
    <polarisation>{polarisation}</polarisation>
    <mode>IW</mode>
    <swath>IW1</swath>
  </adsHeader>
  <calibrationVectorList count="1">
    <calibrationVector>
      <line>0</line>
      <pixel>{pixel_cols}</pixel>
      <sigmaNought>{sigma_values}</sigmaNought>
      <betaNought>1.0 1.0</betaNought>
      <gamma>1.0 1.0</gamma>
      <dn>1.0 1.0</dn>
    </calibrationVector>
  </calibrationVectorList>
</calibration>"""


def _make_synthetic_geotiff_bytes(
    width: int = 10,
    height: int = 10,
    dn_value: int = 100,
    custom_array: np.ndarray | None = None,
    crs: str | None = "EPSG:4326",
    transform: rasterio.Affine | None = None,
    gcps: list[Any] | None = None,
) -> bytes:
    buf = io.BytesIO()
    if custom_array is not None:
        data = custom_array.astype(np.uint16)
        h, w = data.shape
    else:
        data = np.full((height, width), dn_value, dtype=np.uint16)
        h, w = height, width

    open_kwargs: dict[str, Any] = {
        "driver": "GTiff",
        "height": h,
        "width": w,
        "count": 1,
        "dtype": "uint16",
    }
    if gcps is not None:
        open_kwargs["gcps"] = gcps
        open_kwargs["crs"] = crs or "EPSG:4326"
    else:
        open_kwargs["transform"] = transform if transform is not None else from_origin(8.0, 52.0, 0.001, 0.001)
        if crs is not None:
            open_kwargs["crs"] = crs

    with rasterio.open(buf, "w", **open_kwargs) as dst:
        dst.write(data, 1)

    return buf.getvalue()


def _make_synthetic_s1_zip(
    safe_dir: str = _SAFE_DIR,
    include_measurement: bool = True,
    include_calibration: bool = True,
    polarisation: str = "VV",
    pixel_cols: str = "0 9",
    sigma_values: str = "10.0 10.0",
    custom_dn_array: np.ndarray | None = None,
    malformed_cal_xml: str | None = None,
    measurement_crs: str | None = "EPSG:4326",
    measurement_transform: rasterio.Affine | None = None,
    measurement_gcps: list[Any] | None = None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr(f"{safe_dir}/", "")
        zf.writestr(
            f"{safe_dir}/manifest.safe",
            "<?xml version=\"1.0\"?><XFDU><metadataSection/></XFDU>",
        )

        if include_measurement:
            tiff_bytes = _make_synthetic_geotiff_bytes(
                custom_array=custom_dn_array,
                crs=measurement_crs,
                transform=measurement_transform,
                gcps=measurement_gcps,
            )
            fname = f"s1a-iw-grd-{polarisation.lower()}-20181008t063000-20181008t063025-023855-029b3d-001.tiff"
            zf.writestr(f"{safe_dir}/measurement/{fname}", tiff_bytes)

        if include_calibration:
            if malformed_cal_xml is not None:
                cal_xml = malformed_cal_xml
            else:
                cal_xml = _CALIBRATION_XML_TEMPLATE.format(
                    polarisation=polarisation,
                    pixel_cols=pixel_cols,
                    sigma_values=sigma_values,
                )
            fname_cal = f"calibration-s1a-iw-grd-{polarisation.lower()}-20181008t063000-20181008t063025-023855-029b3d-001.xml"
            zf.writestr(f"{safe_dir}/annotation/calibration/{fname_cal}", cal_xml)

    return buf.getvalue()


def _source_asset(location: Path | str) -> Asset:
    retrieved = datetime.now(timezone.utc)
    return Asset(
        id="asset-src-123",
        investigation_id="inv-b2-test",
        type=AssetType.SATELLITE_SCENE,
        provider="sentinel1",
        source="local_file",
        location=str(location),
        acquisition_time=retrieved,
        provenance=Provenance(
            product_id="test-synthetic-s1-id",
            retrieved_at=retrieved,
            extra={"product_name": _SAFE_DIR},
        ),
        metadata={"provider": "sentinel1"},
    )


def _scene(asset: Asset) -> SatelliteScene:
    return SatelliteScene(
        id="scene-b2-123",
        investigation_id=asset.investigation_id,
        asset_id=asset.id,
        provider="sentinel1",
        sensor="SENTINEL-1-A SAR",
        acquisition_time=asset.acquisition_time or datetime.now(timezone.utc),
        footprint=PolygonAreaOfInterest(
            kind="polygon",
            coordinates=[[[8.0, 51.5], [9.0, 51.5], [9.0, 52.0], [8.0, 52.0], [8.0, 51.5]]],
        ),
        metadata={"product_name": _SAFE_DIR, "sensor_mode": "IW", "product_type": "GRD"},
    )


class Sentinel1PreprocessingUnitTests(unittest.TestCase):
    def test_parse_calibration_xml_valid(self) -> None:
        xml = _CALIBRATION_XML_TEMPLATE.format(
            polarisation="VV",
            pixel_cols="0 5 9",
            sigma_values="10.0 10.0 10.0",
        ).encode("utf-8")

        pol, vectors = parse_calibration_xml(xml)
        self.assertEqual(pol, "VV")
        self.assertEqual(len(vectors), 1)
        self.assertEqual(vectors[0].line, 0)
        np.testing.assert_array_equal(vectors[0].pixels, [0, 5, 9])
        np.testing.assert_array_equal(vectors[0].sigma_nought, [10.0, 10.0, 10.0])

    def test_build_calibration_lut_1d(self) -> None:
        xml = _CALIBRATION_XML_TEMPLATE.format(
            polarisation="VV",
            pixel_cols="0 9",
            sigma_values="10.0 20.0",
        ).encode("utf-8")
        _, vectors = parse_calibration_xml(xml)

        lut_1d = build_calibration_lut_1d(vectors, image_width=10)
        self.assertEqual(len(lut_1d), 10)
        self.assertAlmostEqual(lut_1d[0], 10.0)
        self.assertAlmostEqual(lut_1d[9], 20.0)

    def test_compute_sigma0_db_numerical_exactness(self) -> None:
        # Array with known DN values:
        # DN = 100, gain = 10 -> sigma0_linear = (100/10)^2 = 100 -> sigma0_db = 10*log10(100) = 20.0 dB
        # DN = 10,  gain = 10 -> sigma0_linear = (10/10)^2  = 1   -> sigma0_db = 10*log10(1)   = 0.0 dB
        # DN = 1,   gain = 10 -> sigma0_linear = (1/10)^2   = 0.01-> sigma0_db = 10*log10(0.01)= -20.0 dB
        # DN = 0,   gain = 10 -> zero/invalid -> NaN
        dn_array = np.array([[100, 10], [1, 0]], dtype=np.uint16)
        lut_1d = np.array([10.0, 10.0], dtype=np.float64)

        sigma0_db, stats = compute_sigma0_db(dn_array, lut_1d)

        self.assertEqual(sigma0_db.shape, (2, 2))
        self.assertAlmostEqual(sigma0_db[0, 0], 20.0, places=4)
        self.assertAlmostEqual(sigma0_db[0, 1], 0.0, places=4)
        self.assertAlmostEqual(sigma0_db[1, 0], -20.0, places=4)
        self.assertTrue(np.isnan(sigma0_db[1, 1]))

        self.assertEqual(stats["invalid_pixel_count"], 1)
        self.assertEqual(stats["finite_pixel_count"], 3)
        self.assertAlmostEqual(stats["min_db"], -20.0, places=4)
        self.assertAlmostEqual(stats["max_db"], 20.0, places=4)


class Sentinel1PreprocessingPipelineTests(unittest.TestCase):
    def test_valid_synthetic_grd_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_bytes = _make_synthetic_s1_zip(polarisation="VV")
            zip_path = Path(tmp) / "synthetic_product.zip"
            zip_path.write_bytes(zip_bytes)

            asset = _source_asset(zip_path)
            scene = _scene(asset)
            registry = InMemoryAssetRegistry()

            derived_asset, meta = preprocess_sentinel1_scene(
                investigation_id="inv-b2-test",
                scene=scene,
                asset=asset,
                output_dir=Path(tmp) / "derived",
                registry=registry,
            )

            # 1. Measurement raster discovery & polarization discovery
            self.assertEqual(meta["polarizations"], ["VV"])
            self.assertIn("source_measurement_files", meta)

            # 2. Output GeoTIFF creation
            derived_path = Path(derived_asset.location)
            self.assertTrue(derived_path.exists())
            self.assertTrue(derived_path.name.endswith(".tif"))

            # 3. Output geospatial metadata
            with rasterio.open(derived_path) as src:
                self.assertEqual(src.width, 10)
                self.assertEqual(src.height, 10)
                self.assertEqual(src.count, 1)
                self.assertEqual(src.crs.to_epsg(), 4326)
                band1 = src.read(1)
                # Verify numeric calibration result: DN=100, gain=10 -> 20.0 dB
                self.assertAlmostEqual(float(band1[0, 0]), 20.0, places=3)

            # 4. Metadata population
            self.assertEqual(meta["parent_asset_id"], asset.id)
            self.assertEqual(meta["parent_scene_id"], scene.id)
            self.assertEqual(meta["calibration"], "sigma0_db")
            self.assertEqual(meta["output_units"], "dB")
            self.assertEqual(meta["finite_pixel_count"], 100)
            self.assertEqual(meta["invalid_pixel_count"], 0)
            self.assertAlmostEqual(meta["mean_db"], 20.0, places=3)

            # 5. Derived Asset registration
            self.assertEqual(derived_asset.type, AssetType.IMAGERY_PREVIEW)
            self.assertEqual(derived_asset.investigation_id, "inv-b2-test")
            self.assertEqual(derived_asset.provenance.extra["parent_asset_id"], asset.id)

    def test_zero_and_invalid_pixel_handling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Array with zeros
            arr = np.full((10, 10), 100, dtype=np.uint16)
            arr[0, 0] = 0
            arr[0, 1] = 0

            zip_bytes = _make_synthetic_s1_zip(custom_dn_array=arr)
            zip_path = Path(tmp) / "zeros.zip"
            zip_path.write_bytes(zip_bytes)

            asset = _source_asset(zip_path)
            scene = _scene(asset)

            derived_asset, meta = preprocess_sentinel1_scene(
                investigation_id="inv-b2-test",
                scene=scene,
                asset=asset,
                output_dir=Path(tmp) / "derived",
            )

            self.assertEqual(meta["invalid_pixel_count"], 2)
            self.assertEqual(meta["finite_pixel_count"], 98)

    def test_missing_measurement_raster_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_bytes = _make_synthetic_s1_zip(include_measurement=False)
            zip_path = Path(tmp) / "no_measurement.zip"
            zip_path.write_bytes(zip_bytes)

            asset = _source_asset(zip_path)
            scene = _scene(asset)

            with self.assertRaises(Sentinel1PreprocessingError) as ctx:
                preprocess_sentinel1_scene("inv-b2-test", scene, asset, output_dir=Path(tmp))
            self.assertIn("measurement", str(ctx.exception).lower())

    def test_missing_calibration_metadata_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_bytes = _make_synthetic_s1_zip(include_calibration=False)
            zip_path = Path(tmp) / "no_cal.zip"
            zip_path.write_bytes(zip_bytes)

            asset = _source_asset(zip_path)
            scene = _scene(asset)

            with self.assertRaises(Sentinel1PreprocessingError) as ctx:
                preprocess_sentinel1_scene("inv-b2-test", scene, asset, output_dir=Path(tmp))
            self.assertIn("calibration", str(ctx.exception).lower())

    def test_malformed_calibration_xml_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_bytes = _make_synthetic_s1_zip(malformed_cal_xml="<<not valid xml>>")
            zip_path = Path(tmp) / "bad_xml.zip"
            zip_path.write_bytes(zip_bytes)

            asset = _source_asset(zip_path)
            scene = _scene(asset)

            with self.assertRaises(Sentinel1PreprocessingError) as ctx:
                preprocess_sentinel1_scene("inv-b2-test", scene, asset, output_dir=Path(tmp))
            self.assertIn("calibration", str(ctx.exception).lower())

    def test_source_zip_hash_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_bytes = _make_synthetic_s1_zip()
            zip_path = Path(tmp) / "readonly_test.zip"
            zip_path.write_bytes(zip_bytes)

            hash_before = hashlib.sha256(zip_bytes).hexdigest()

            asset = _source_asset(zip_path)
            scene = _scene(asset)
            preprocess_sentinel1_scene("inv-b2-test", scene, asset, output_dir=Path(tmp) / "derived")

            hash_after = hashlib.sha256(zip_path.read_bytes()).hexdigest()
            self.assertEqual(hash_before, hash_after)

    def test_deterministic_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            zip_bytes = _make_synthetic_s1_zip()
            zip_path = Path(tmp) / "product.zip"
            zip_path.write_bytes(zip_bytes)

            asset = _source_asset(zip_path)
            scene = _scene(asset)

            # Pass None output_dir to use default deterministic pathing
            derived_asset, _ = preprocess_sentinel1_scene("inv-b2-test", scene, asset)
            loc = Path(derived_asset.location)

            self.assertIn("derived", loc.parts)
            self.assertIn("inv-b2-test", loc.parts)
            self.assertIn("sar", loc.parts)
            self.assertIn(scene.id, loc.parts)
            self.assertEqual(loc.name, "sentinel1_sigma0_db.tif")

    def test_identity_transform_rejected_fails_closed(self) -> None:
        """Ensure measurement rasters with identity transform are rejected when georeferencing is expected."""
        with tempfile.TemporaryDirectory() as tmp:
            zip_bytes = _make_synthetic_s1_zip(measurement_transform=Affine.identity())
            zip_path = Path(tmp) / "identity_transform.zip"
            zip_path.write_bytes(zip_bytes)

            asset = _source_asset(zip_path)
            scene = _scene(asset)

            with self.assertRaises(Sentinel1PreprocessingError) as ctx:
                preprocess_sentinel1_scene("inv-b2-test", scene, asset, output_dir=Path(tmp))
            self.assertIn("identity transform", str(ctx.exception).lower())

    def test_missing_crs_rejected_fails_closed(self) -> None:
        """Ensure CRS is not silently omitted or assumed when missing from measurement raster."""
        with tempfile.TemporaryDirectory() as tmp:
            zip_bytes = _make_synthetic_s1_zip(measurement_crs=None)
            zip_path = Path(tmp) / "missing_crs.zip"
            zip_path.write_bytes(zip_bytes)

            asset = _source_asset(zip_path)
            scene = _scene(asset)

            with self.assertRaises(Sentinel1PreprocessingError) as ctx:
                preprocess_sentinel1_scene("inv-b2-test", scene, asset, output_dir=Path(tmp))
            self.assertIn("crs metadata", str(ctx.exception).lower())

    def test_native_gcp_raster_pipeline_success(self) -> None:
        """Ensure native SAR with GCPs produces calibrated GeoTIFF preserving GCPs for downstream geocoding."""
        with tempfile.TemporaryDirectory() as tmp:
            gcps = [
                GroundControlPoint(row=0.0, col=0.0, x=8.0, y=52.0, z=0.0, id="1"),
                GroundControlPoint(row=10.0, col=10.0, x=9.0, y=53.0, z=0.0, id="2"),
            ]
            zip_bytes = _make_synthetic_s1_zip(measurement_crs="EPSG:4326", measurement_gcps=gcps)
            zip_path = Path(tmp) / "gcp_product.zip"
            zip_path.write_bytes(zip_bytes)

            asset = _source_asset(zip_path)
            scene = _scene(asset)

            derived_asset, meta = preprocess_sentinel1_scene("inv-b2-test", scene, asset, output_dir=Path(tmp))
            self.assertEqual(meta["georeferencing"], "native_gcp")
            self.assertTrue(Path(derived_asset.location).name.endswith("sentinel1_sigma0_db_native.tif"))
            with rasterio.open(derived_asset.location) as src:
                self.assertEqual(len(src.gcps[0]), 2)

    def test_authoritative_georeferencing_metadata_preserved(self) -> None:
        """Verify authoritative CRS, transform, bounds, and pixel size are correctly derived and preserved."""
        with tempfile.TemporaryDirectory() as tmp:
            custom_transform = from_origin(9.5, 45.2, 0.0001, 0.0001)
            zip_bytes = _make_synthetic_s1_zip(
                measurement_crs="EPSG:4326",
                measurement_transform=custom_transform,
            )
            zip_path = Path(tmp) / "georef_product.zip"
            zip_path.write_bytes(zip_bytes)

            asset = _source_asset(zip_path)
            scene = _scene(asset)

            derived_asset, meta = preprocess_sentinel1_scene(
                "inv-b2-test", scene, asset, output_dir=Path(tmp) / "derived"
            )

            # CRS not omitted
            self.assertEqual(meta["crs"], "EPSG:4326")
            # Bounds sensible and derived from transform
            self.assertAlmostEqual(meta["bounds"]["west"], 9.5)
            self.assertAlmostEqual(meta["bounds"]["north"], 45.2)
            self.assertAlmostEqual(meta["bounds"]["east"], 9.5 + 10 * 0.0001)
            self.assertAlmostEqual(meta["bounds"]["south"], 45.2 - 10 * 0.0001)
            # Pixel size positive
            self.assertAlmostEqual(meta["pixel_size"][0], 0.0001)
            self.assertAlmostEqual(meta["pixel_size"][1], 0.0001)


if __name__ == "__main__":
    unittest.main()
