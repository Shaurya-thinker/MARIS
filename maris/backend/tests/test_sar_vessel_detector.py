"""Unit tests for Phase 6.1 — Core SAR Bright-Target Detector.

Validates the algorithmic correctness, parameter configurability, spatial georeferencing,
guard-band isolation, and scientific integrity of SarBrightTargetDetector.

SCIENTIFIC TESTING NOTICE:
These tests establish algorithmic and computational correctness on synthetic arrays
and controlled test fixtures. They do NOT establish empirical probability of detection
(Pd) or false alarm rate (Pfa) on arbitrary unvalidated real-world Sentinel-1 scenes.
"""

from __future__ import annotations

import math
from pathlib import Path
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.services.real_experiment.sar_vessel_detector import (
    SarBrightTarget,
    SarBrightTargetDetector,
    SarBrightTargetDetectorError,
)
from app.services.maritime_mask import MaritimeMasker


@pytest.fixture
def base_transform():
    """Create a standard North-Up geographic transform in WGS84."""
    # Origin at (9.40°E, 43.30°N), resolution 0.00025° (~20m x ~27m)
    return from_origin(9.40, 43.30, 0.00025, 0.00025)


@pytest.fixture
def synthetic_sea_raster():
    """Create a 100x100 synthetic sea clutter raster with Gaussian backscatter."""
    rng = np.random.default_rng(seed=42)
    # Ambient sea clutter: mean -10.0 dB, std 1.2 dB
    clutter = rng.normal(loc=-10.0, scale=1.2, size=(100, 100)).astype(np.float32)
    return clutter


class TestSarBrightTargetDetector:
    """Algorithmic unit test suite for SarBrightTargetDetector."""

    def test_01_bright_point_target_detection(self, synthetic_sea_raster, base_transform):
        """1. A synthetic bright point target is detected at the expected coordinate."""
        raster = synthetic_sea_raster.copy()
        # Inject strong point target (+12 dB) at pixel (row=50, col=50)
        raster[50, 50] = 12.0

        detector = SarBrightTargetDetector(
            cfar_k_sigma=4.0,
            guard_window_pixels=5,
            clutter_window_pixels=31,
            min_target_pixels=1,
            max_target_pixels=150,
            coastal_buffer_m=0.0,
        )

        targets = detector.detect_from_raster(
            raster=raster,
            transform=base_transform,
            apply_maritime_mask=False,
        )

        assert len(targets) >= 1
        # Find the target at (50, 50)
        tgt = targets[0]
        assert abs(tgt.pixel_x - 50.0) < 0.5
        assert abs(tgt.pixel_y - 50.0) < 0.5
        assert tgt.peak_backscatter_db == pytest.approx(12.0, abs=0.1)
        assert tgt.local_clutter_mean_db == pytest.approx(-10.0, abs=0.5)
        assert tgt.target_to_clutter_ratio_db == pytest.approx(22.0, abs=0.6)
        assert tgt.pixel_count == 1

    def test_02_tcr_threshold_behavior(self, synthetic_sea_raster, base_transform):
        """2. Strong targets are detected, but weak targets below TCR or k_sigma are rejected."""
        raster = synthetic_sea_raster.copy()
        # Inject strong target at (30, 30) with TCR ~ 15 dB
        raster[30, 30] = 5.0
        # Inject weak target at (70, 70) with backscatter barely above mean (-8.0 dB vs mean -10.0 dB, TCR ~ 2 dB)
        raster[70, 70] = -8.0

        detector = SarBrightTargetDetector(
            cfar_k_sigma=4.0,
            min_tcr_db=5.0,  # requires at least 5 dB contrast
            guard_window_pixels=5,
            clutter_window_pixels=31,
        )

        targets = detector.detect_from_raster(
            raster=raster,
            transform=base_transform,
            apply_maritime_mask=False,
        )

        # Strong target must be detected
        strong_targets = [t for t in targets if abs(t.pixel_x - 30) < 1.0 and abs(t.pixel_y - 30) < 1.0]
        assert len(strong_targets) == 1

        # Weak target at (70, 70) must NOT be detected
        weak_targets = [t for t in targets if abs(t.pixel_x - 70) < 1.0 and abs(t.pixel_y - 70) < 1.0]
        assert len(weak_targets) == 0

    def test_03_guard_band_isolation(self, synthetic_sea_raster, base_transform):
        """3. An intense bright target does not substantially contaminate its own clutter estimate."""
        raster = synthetic_sea_raster.copy()
        target_val = 25.0  # very bright target (+25 dB)
        raster[50, 50] = target_val

        # Detector WITH 5x5 guard window
        detector_with_guard = SarBrightTargetDetector(
            guard_window_pixels=5,
            clutter_window_pixels=25,
            cfar_k_sigma=3.5,
        )

        targets = detector_with_guard.detect_from_raster(
            raster=raster,
            transform=base_transform,
            apply_maritime_mask=False,
        )

        assert len(targets) >= 1
        tgt = targets[0]
        # With guard band, clutter mean should remain close to sea background (~ -10 dB), not inflated
        assert tgt.local_clutter_mean_db < -8.5
        assert tgt.target_to_clutter_ratio_db > 30.0

    def test_04_uniform_synthetic_clutter_behavior(self, synthetic_sea_raster, base_transform):
        """4. Pure sea clutter without targets produces zero or near-zero false alarms at k=4.5."""
        detector = SarBrightTargetDetector(
            cfar_k_sigma=4.5,  # 4.5 sigma above mean
            guard_window_pixels=5,
            clutter_window_pixels=31,
            min_tcr_db=6.0,
        )

        targets = detector.detect_from_raster(
            raster=synthetic_sea_raster,
            transform=base_transform,
            apply_maritime_mask=False,
        )

        # In a 100x100 Gaussian array at 4.5 sigma + 6 dB TCR, false detections should be 0
        assert len(targets) == 0

    def test_05_connected_component_grouping(self, synthetic_sea_raster, base_transform):
        """5. Multiple adjacent bright pixels become ONE target; distinct regions become separate targets."""
        raster = synthetic_sea_raster.copy()

        # Cluster 1: 2x2 contiguous block at (30..31, 30..31)
        raster[30:32, 30:32] = 10.0

        # Cluster 2: 1-pixel target at (70, 70), separated by 40 pixels
        raster[70, 70] = 8.0

        detector = SarBrightTargetDetector(
            cfar_k_sigma=4.0,
            min_tcr_db=5.0,
            min_target_pixels=1,
            max_target_pixels=150,
        )

        targets = detector.detect_from_raster(
            raster=raster,
            transform=base_transform,
            apply_maritime_mask=False,
        )

        assert len(targets) == 2
        # Target 1 (the 2x2 cluster)
        t1 = next(t for t in targets if abs(t.pixel_x - 30.5) < 0.5)
        assert t1.pixel_count == 4
        assert t1.pixel_x == pytest.approx(30.5, abs=0.1)
        assert t1.pixel_y == pytest.approx(30.5, abs=0.1)

        # Target 2 (the single pixel)
        t2 = next(t for t in targets if abs(t.pixel_x - 70) < 0.5)
        assert t2.pixel_count == 1

    def test_06_component_size_filtering(self, synthetic_sea_raster, base_transform):
        """6. Components exceeding max_target_pixels (e.g. land slivers/clouds) are rejected."""
        raster = synthetic_sea_raster.copy()

        # Inject massive bright block (10x10 = 100 pixels)
        raster[40:50, 40:50] = 15.0
        # Inject small valid target (2x2 = 4 pixels)
        raster[80:82, 80:82] = 12.0

        detector = SarBrightTargetDetector(
            cfar_k_sigma=4.0,
            min_tcr_db=5.0,
            min_target_pixels=1,
            max_target_pixels=10,  # reject clusters larger than 10 pixels
        )

        targets = detector.detect_from_raster(
            raster=raster,
            transform=base_transform,
            apply_maritime_mask=False,
        )

        # The 100-pixel block must be rejected
        assert not any(t.pixel_count >= 100 for t in targets)
        # The 4-pixel target must be accepted
        accepted = [t for t in targets if abs(t.pixel_x - 80.5) < 0.5]
        assert len(accepted) == 1
        assert accepted[0].pixel_count == 4

    def test_07_maritime_masking_rejects_land_candidates(self, synthetic_sea_raster, base_transform, tmp_path):
        """7. Bright candidates inside land or coastal exclusion area are rejected by MaritimeMasker."""
        raster = synthetic_sea_raster.copy()
        # Inject two identical bright targets
        raster[20, 20] = 15.0  # Will be inside land
        raster[80, 80] = 15.0  # Will be in open ocean

        # Create a small mock maritime mask: top half (rows 0..49) is land, bottom half (50..99) is ocean
        mock_ocean_mask = np.zeros((100, 100), dtype=bool)
        mock_ocean_mask[50:, :] = True  # Ocean in bottom half only

        detector = SarBrightTargetDetector(cfar_k_sigma=4.0, min_tcr_db=5.0)

        # Run with explicit valid_mask acting as maritime domain
        targets = detector.detect_from_raster(
            raster=raster,
            transform=base_transform,
            valid_mask=mock_ocean_mask,
            apply_maritime_mask=False,
        )

        # Target at (20, 20) on land must be excluded
        assert not any(abs(t.pixel_y - 20) < 1.0 for t in targets)
        # Target at (80, 80) in ocean must be retained
        ocean_tgt = [t for t in targets if abs(t.pixel_y - 80) < 1.0]
        assert len(ocean_tgt) == 1

    def test_08_geographic_coordinate_conversion(self, synthetic_sea_raster, base_transform):
        """8. Centroid pixel coordinates are accurately transformed to WGS84 geographic coords."""
        raster = synthetic_sea_raster.copy()
        raster[40, 60] = 15.0

        detector = SarBrightTargetDetector(cfar_k_sigma=4.0, min_tcr_db=5.0)
        targets = detector.detect_from_raster(
            raster=raster,
            transform=base_transform,
            apply_maritime_mask=False,
        )

        assert len(targets) == 1
        tgt = targets[0]
        # Expected coordinates: origin lon=9.40, res=0.00025 -> lon = 9.40 + 60*0.00025 = 9.4150
        # origin lat=43.30, res=-0.00025 -> lat = 43.30 - 40*0.00025 = 43.2900
        expected_lon, expected_lat = rasterio.transform.xy(base_transform, 40, 60)
        assert tgt.lon == pytest.approx(expected_lon, abs=1e-5)
        assert tgt.lat == pytest.approx(expected_lat, abs=1e-5)

    def test_09_apparent_radar_extent_estimation(self, synthetic_sea_raster, base_transform):
        """9. Known elongated component produces sensible major and minor apparent radar extents."""
        raster = synthetic_sea_raster.copy()
        # Inject an elongated horizontal bar: length 7 pixels along X, 1 pixel along Y
        raster[50, 45:52] = 12.0

        detector = SarBrightTargetDetector(cfar_k_sigma=4.0, min_tcr_db=5.0)
        targets = detector.detect_from_raster(
            raster=raster,
            transform=base_transform,
            apply_maritime_mask=False,
        )

        assert len(targets) == 1
        tgt = targets[0]
        assert tgt.apparent_major_extent_m is not None
        assert tgt.apparent_minor_extent_m is not None
        # Major extent along 7 pixels (~140m at 20m/px) should be substantially larger than minor (~20m)
        assert tgt.apparent_major_extent_m > tgt.apparent_minor_extent_m * 1.8
        assert tgt.apparent_major_extent_m > 70.0

    def test_10_parameter_validation_and_configurability(self):
        """10. Detector validates parameter constraints and responds to configuration changes."""
        # Validation checks
        with pytest.raises(SarBrightTargetDetectorError):
            SarBrightTargetDetector(cfar_k_sigma=-1.0)
        with pytest.raises(SarBrightTargetDetectorError):
            SarBrightTargetDetector(guard_window_pixels=4)  # must be odd
        with pytest.raises(SarBrightTargetDetectorError):
            SarBrightTargetDetector(clutter_window_pixels=5, guard_window_pixels=5)  # clutter must be > guard
        with pytest.raises(SarBrightTargetDetectorError):
            SarBrightTargetDetector(min_target_pixels=10, max_target_pixels=5)

        # Valid initialization
        det = SarBrightTargetDetector(
            cfar_k_sigma=5.5,
            guard_window_pixels=7,
            clutter_window_pixels=51,
            min_target_pixels=2,
            max_target_pixels=80,
        )
        assert det.cfar_k_sigma == 5.5
        assert det.guard_window_pixels == 7
        assert det.clutter_window_pixels == 51

    def test_11_nan_and_nodata_robustness(self, synthetic_sea_raster, base_transform):
        """11. Detector gracefully ignores NaN, Inf, and noise-floor values without crashing."""
        raster = synthetic_sea_raster.copy()
        # Inject NaNs, Infs, and extreme negative noise-floor values
        raster[0:10, 0:10] = np.nan
        raster[15:20, 15:20] = np.inf
        raster[25:30, 25:30] = -999.0
        # Inject one valid target in clean region
        raster[75, 75] = 15.0

        detector = SarBrightTargetDetector(cfar_k_sigma=3.5)
        targets = detector.detect_from_raster(
            raster=raster,
            transform=base_transform,
            apply_maritime_mask=False,
        )

        assert len(targets) == 1
        assert abs(targets[0].pixel_x - 75) < 1.0
        assert math.isfinite(targets[0].peak_backscatter_db)
        assert math.isfinite(targets[0].local_clutter_mean_db)

    def test_12_scientific_terminology_and_schema(self, synthetic_sea_raster, base_transform):
        """12. Output conforms to SarBrightTarget schema with zero RCS/hull/Doppler fields."""
        raster = synthetic_sea_raster.copy()
        raster[50, 50] = 12.0

        detector = SarBrightTargetDetector()
        targets = detector.detect_from_raster(
            raster=raster,
            transform=base_transform,
            apply_maritime_mask=False,
        )

        assert len(targets) >= 1
        t = targets[0]
        assert isinstance(t, SarBrightTarget)

        # Verify prohibited terms are NOT attributes of SarBrightTarget
        prohibited = [
            "rcs", "rcs_db", "rcs_dbsm", "hull_length", "hull_width",
            "vessel_length", "vessel_width", "doppler_shift", "doppler_velocity",
            "is_vessel", "is_dark_vessel"
        ]
        for field_name in prohibited:
            assert not hasattr(t, field_name), f"Prohibited field '{field_name}' found on SarBrightTarget!"

        d = t.as_dict()
        assert d["provenance"] == "SAR_BRIGHT_TARGET_DETECTION"
        assert "target_to_clutter_ratio_db" in d
        assert "apparent_major_extent_m" in d
        assert "peak_backscatter_db" in d

    def test_13_oil_slick_pixels_not_flagged_as_bright_targets(self):
        """13. Verified Cap Corse GeoTIFF fixture: dark oil slick pixels are NEVER detected as bright targets."""
        fixture_path = Path(__file__).resolve().parents[1] / "data" / "sar_subscenes" / "corsica_2018_sar_subscene.tif"
        if not fixture_path.exists():
            pytest.skip("corsica_2018_sar_subscene.tif fixture not present on disk")

        detector = SarBrightTargetDetector(
            cfar_k_sigma=4.0,
            min_tcr_db=4.0,
        )

        targets = detector.detect_from_file(fixture_path, apply_maritime_mask=False)

        # In corsica_2018_sar_subscene.tif, the oil slick is at centroid (43.235°N, 9.458°E) with backscatter -18 to -30 dB.
        # Ensure no target is detected with negative contrast or inside deep slick center.
        for t in targets:
            assert t.peak_backscatter_db > -12.0
            assert t.target_to_clutter_ratio_db >= 4.0
