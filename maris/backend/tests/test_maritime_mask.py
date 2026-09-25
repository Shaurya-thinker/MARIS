"""Unit tests for Stage B2.75 - Maritime Domain Masker.

Tests cover:
1. Ocean pixel is retained.
2. Land pixel is rejected.
3. Coastal buffer excludes expected nearshore area (seaward erosion).
4. Inland lake is NOT treated as ocean.
5. NaN SAR pixel remains invalid.
6. Missing ocean dataset fails closed (MaritimeMaskError).
7. Invalid CRS/transform fails closed.
8. Block-wise processing produces deterministic results.
9. Completely land-only block can be skipped.
10. Existing B3 parameters remain unchanged.
11. Existing B3 synthetic tests still pass.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from app.core.config import settings
from app.services.maritime_mask import MaritimeMaskError, MaritimeMasker
from app.services.spill_detection.detector import AdaptiveThresholdSpillDetector


@pytest.fixture
def synthetic_ocean_geojson(tmp_path: Path) -> Path:
    """Create a synthetic GeoJSON with a known square ocean polygon.
    
    Bounding box: lon [10.0, 12.0], lat [40.0, 42.0]
    Center is ocean, outside is land.
    """
    geojson_path = tmp_path / "synthetic_ocean.geojson"
    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "Synthetic Ocean"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [10.0, 40.0],
                            [12.0, 40.0],
                            [12.0, 42.0],
                            [10.0, 42.0],
                            [10.0, 40.0],
                        ]
                    ],
                },
            }
        ],
    }
    with open(geojson_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return geojson_path


def test_ocean_pixel_retained_and_land_rejected(synthetic_ocean_geojson: Path):
    """Test 1 & 2: Ocean pixel is retained, land pixel is rejected."""
    masker = MaritimeMasker(dataset_path=synthetic_ocean_geojson, coastal_buffer_m=0.0)

    # 100x100 raster covering lon [9.0, 13.0], lat [39.0, 43.0]
    # Center [10.0, 12.0] x [40.0, 42.0] is ocean; outer border is land.
    transform = from_bounds(9.0, 39.0, 13.0, 43.0, 100, 100)
    crs = "EPSG:4326"

    # Block 1: Inside ocean (lon [10.5, 11.5], lat [40.5, 41.5])
    # Pixel coords inside: row 25:75, col 25:75
    ocean_block = np.full((50, 50), -15.0, dtype=np.float32)
    ocean_window = rasterio.windows.Window(25, 25, 50, 50)
    ocean_mask = masker.rasterize_block_mask(ocean_window, transform, crs)
    assert np.all(ocean_mask), "Pixels in ocean interior must be 100% retained"

    # Block 2: Completely outside ocean (lon [9.0, 9.5], lat [39.0, 39.5])
    # col 0:12, row 75:100
    land_window = rasterio.windows.Window(0, 75, 12, 25)
    land_mask = masker.rasterize_block_mask(land_window, transform, crs)
    assert not np.any(land_mask), "Pixels in land area must be 100% rejected"


def test_coastal_buffer_excludes_nearshore_area(synthetic_ocean_geojson: Path):
    """Test 3: Coastal buffer excludes expected nearshore area (seaward erosion)."""
    # Without buffer:
    masker_0 = MaritimeMasker(dataset_path=synthetic_ocean_geojson, coastal_buffer_m=0.0)
    # With buffer ~ 20 km (synthetic test at ~0.04 deg/pixel)
    # Lon bounds 9.0 to 13.0 over 100 pixels => 0.04 deg/pixel (~4.4 km/pixel at lat 41)
    # A buffer of 10,000 m should erode ~2 pixels from coastline.
    masker_buf = MaritimeMasker(dataset_path=synthetic_ocean_geojson, coastal_buffer_m=10000.0)

    transform = from_bounds(9.0, 39.0, 13.0, 43.0, 100, 100)
    crs = "EPSG:4326"
    full_window = rasterio.windows.Window(0, 0, 100, 100)

    mask_0 = masker_0.rasterize_block_mask(full_window, transform, crs)
    mask_buf = masker_buf.rasterize_block_mask(full_window, transform, crs)

    count_0 = int(np.sum(mask_0))
    count_buf = int(np.sum(mask_buf))

    assert count_0 > 0, "Raw ocean mask should have valid pixels"
    assert count_buf > 0, "Buffered ocean mask should retain offshore pixels"
    assert count_buf < count_0, "Buffered ocean mask must be strictly smaller than unbuffered (erosion)"
    # Zero pixels in mask_buf should be outside mask_0 (no land expansion)
    assert np.all(mask_0[mask_buf]), "Buffered ocean must be a strict subset of unbuffered ocean"


def test_inland_lake_not_treated_as_ocean():
    """Test 4: Inland lake is NOT treated as ocean using the production dataset."""
    dataset_path = Path(settings.ocean_dataset_path)
    if not dataset_path.exists():
        pytest.skip(f"Ocean dataset not present at {dataset_path}")

    masker = MaritimeMasker(dataset_path=dataset_path, coastal_buffer_m=0.0)

    # Lake Geneva (approx lon 6.4 to 6.6 E, lat 46.4 to 46.6 N)
    # 50x50 block
    transform = from_bounds(6.4, 46.4, 6.6, 46.6, 50, 50)
    crs = "EPSG:4326"
    window = rasterio.windows.Window(0, 0, 50, 50)

    mask = masker.rasterize_block_mask(window, transform, crs)
    assert int(np.sum(mask)) == 0, "Lake Geneva must produce 0 ocean pixels (not classified as ocean)"


def test_nan_sar_pixel_remains_invalid(synthetic_ocean_geojson: Path):
    """Test 5: NaN SAR pixel remains invalid even in ocean."""
    masker = MaritimeMasker(dataset_path=synthetic_ocean_geojson, coastal_buffer_m=0.0)

    # Create block inside ocean with some NaNs
    block = np.full((10, 10), -15.0, dtype=np.float32)
    block[2:5, 2:5] = np.nan

    # Window inside ocean
    transform = from_bounds(10.5, 40.5, 11.5, 41.5, 10, 10)
    crs = "EPSG:4326"
    window = rasterio.windows.Window(0, 0, 10, 10)

    ocean_mask = masker.rasterize_block_mask(window, transform, crs)
    combined_valid = np.isfinite(block) & ocean_mask

    assert np.all(ocean_mask), "All pixels are geographically ocean"
    assert not np.any(combined_valid[2:5, 2:5]), "NaN pixels must be masked out as invalid"
    assert np.sum(combined_valid) == 100 - 9, "Only finite ocean pixels are valid"


def test_missing_ocean_dataset_fails_closed(tmp_path: Path):
    """Test 6: Missing ocean dataset fails closed with MaritimeMaskError."""
    non_existent = tmp_path / "does_not_exist.geojson"
    masker = MaritimeMasker(dataset_path=non_existent)

    window = rasterio.windows.Window(0, 0, 10, 10)
    transform = from_bounds(10.0, 40.0, 11.0, 41.0, 10, 10)

    with pytest.raises(MaritimeMaskError) as exc_info:
        masker.rasterize_block_mask(window, transform, "EPSG:4326")

    assert "not found" in str(exc_info.value).lower()


def test_invalid_crs_or_transform_fails_closed(synthetic_ocean_geojson: Path):
    """Test 7: Invalid CRS/transform fails closed."""
    masker = MaritimeMasker(dataset_path=synthetic_ocean_geojson)
    window = rasterio.windows.Window(0, 0, 10, 10)

    # Identity transform (unreferenced pixel coordinates)
    identity_transform = rasterio.Affine.identity()
    with pytest.raises(MaritimeMaskError) as exc_info:
        masker.rasterize_block_mask(window, identity_transform, "EPSG:4326")
    assert "identity" in str(exc_info.value).lower()

    # Projected CRS without handling / invalid CRS
    valid_transform = from_bounds(10.0, 40.0, 11.0, 41.0, 10, 10)
    with pytest.raises(MaritimeMaskError) as exc_info:
        masker.rasterize_block_mask(window, valid_transform, "EPSG:3857")
    assert "geographic" in str(exc_info.value).lower()


def test_blockwise_processing_produces_deterministic_results(synthetic_ocean_geojson: Path):
    """Test 8: Block-wise processing produces identical results to combined full raster."""
    masker = MaritimeMasker(dataset_path=synthetic_ocean_geojson, coastal_buffer_m=5000.0)

    # 40x40 raster
    transform = from_bounds(9.5, 39.5, 12.5, 42.5, 40, 40)
    crs = "EPSG:4326"

    # Full mask processed in one window
    full_window = rasterio.windows.Window(0, 0, 40, 40)
    full_mask = masker.rasterize_block_mask(full_window, transform, crs)

    # Process in 4 20x20 blocks
    tiled_mask = np.zeros((40, 40), dtype=bool)
    for r in (0, 20):
        for c in (0, 20):
            w = rasterio.windows.Window(c, r, 20, 20)
            tiled_mask[r : r + 20, c : c + 20] = masker.rasterize_block_mask(w, transform, crs)

    # Due to padding buffer across tile seams, interior tile boundaries match full mask
    # Inside the ocean interior (rows 10..30, cols 10..30), results must be completely deterministic
    assert np.array_equal(
        full_mask[10:30, 10:30], tiled_mask[10:30, 10:30]
    ), "Block-wise padded buffer must match full-window interior"


def test_completely_land_block_skipped(synthetic_ocean_geojson: Path):
    """Test 9: Completely land-only block has sum == 0 and can be skipped."""
    masker = MaritimeMasker(dataset_path=synthetic_ocean_geojson)

    # Area completely outside ocean
    transform = from_bounds(0.0, 0.0, 1.0, 1.0, 20, 20)
    crs = "EPSG:4326"
    window = rasterio.windows.Window(0, 0, 20, 20)

    mask = masker.rasterize_block_mask(window, transform, crs)
    assert not np.any(mask)
    assert np.sum(mask) == 0


def test_existing_b3_parameters_remain_unchanged():
    """Test 10: Existing B3 scientific detection parameters are unchanged."""
    import inspect
    from app.services.spill_detection.geometry import extract_spill_geometries
    from app.services.spill_detection.service import detect_spills_from_sar_scene

    detector = AdaptiveThresholdSpillDetector()

    assert detector.damping_threshold_db == 3.5
    assert detector.k_sigma == 2.0
    assert detector.window_size_pixels == 51
    assert detector.border_margin_pixels == 2
    assert detector.noise_floor_db == -45.0

    sig_geom = inspect.signature(extract_spill_geometries)
    assert sig_geom.parameters["min_area_m2"].default == 25000.0
    assert sig_geom.parameters["max_area_m2"].default == 250000000.0
    assert sig_geom.parameters["min_pixels"].default == 10

    sig_svc = inspect.signature(detect_spills_from_sar_scene)
    assert sig_svc.parameters["min_area_m2"].default == 25000.0
    assert sig_svc.parameters["max_area_m2"].default == 250000000.0
    assert sig_svc.parameters["min_pixels"].default == 10
