"""MARIS Stage B3 — Spill Detection & Geometry package.

Provides modular SAR oil spill candidate detection, adaptive background thresholding,
and geospatial vector geometry extraction for analysis-ready Sentinel-1 rasters.
"""

from app.services.spill_detection.base import (
    BaseSpillDetector,
    DetectorResult,
    SpillDetectionError,
    SpillDetectionResult,
    SpillRegionStats,
)
from app.services.spill_detection.detector import AdaptiveThresholdSpillDetector
from app.services.spill_detection.geometry import (
    compute_pixel_area_m2,
    extract_spill_geometries,
    to_geojson_feature_collection,
)
from app.services.spill_detection.service import (
    detect_spills_from_sar_scene,
    select_polarization_band,
)

__all__ = [
    "AdaptiveThresholdSpillDetector",
    "BaseSpillDetector",
    "DetectorResult",
    "SpillDetectionError",
    "SpillDetectionResult",
    "SpillRegionStats",
    "compute_pixel_area_m2",
    "detect_spills_from_sar_scene",
    "extract_spill_geometries",
    "select_polarization_band",
    "to_geojson_feature_collection",
]
