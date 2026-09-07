"""Vector geometry extraction and connected-component spatial filtering for Stage B3.

Converts 2D detection masks and continuous probability rasters into GeoJSON-compliant
spill geometries (Polygon / MultiPolygon) with quantitative physical metrics
(area in m2/km2, bounding box, centroid, damping contrast, confidence).
"""

from __future__ import annotations

from collections import deque
import math
from typing import Any

import numpy as np
import rasterio.features
from rasterio.crs import CRS
from rasterio.transform import Affine

from app.services.spill_detection.base import (
    SpillDetectionError,
    SpillDetectionResult,
    SpillRegionStats,
)

try:
    import scipy.ndimage as ndi
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


def _label_connected_components(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """Label connected components using 8-connectivity with SciPy or pure-Python BFS fallback."""
    if _HAS_SCIPY:
        # 8-connectivity structuring element
        structure = np.ones((3, 3), dtype=int)
        labeled, num_features = ndi.label(mask, structure=structure)
        return labeled, int(num_features)

    # Pure NumPy/Python BFS fallback if scipy is not installed
    height, width = mask.shape
    labeled = np.zeros((height, width), dtype=np.int32)
    current_label = 0

    visited = np.zeros((height, width), dtype=bool)

    for y in range(height):
        for x in range(width):
            if mask[y, x] and not visited[y, x]:
                current_label += 1
                queue = deque([(y, x)])
                visited[y, x] = True
                labeled[y, x] = current_label

                while queue:
                    cy, cx = queue.popleft()
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            if dy == 0 and dx == 0:
                                continue
                            ny, nx = cy + dy, cx + dx
                            if 0 <= ny < height and 0 <= nx < width:
                                if mask[ny, nx] and not visited[ny, nx]:
                                    visited[ny, nx] = True
                                    labeled[ny, nx] = current_label
                                    queue.append((ny, nx))

    return labeled, current_label


def compute_pixel_area_m2(transform: Affine, crs: CRS | None, center_lat: float = 0.0) -> float:
    """Calculate the ground area in square meters represented by a single raster pixel."""
    pixel_x = abs(transform.a)
    pixel_y = abs(transform.e)

    # Check if CRS is geographic (degrees) or projected (meters)
    is_geographic = crs.is_geographic if crs is not None else True

    if is_geographic:
        # WGS84 ellipsoidal approximation for degrees to meters at center_lat
        lat_rad = math.radians(center_lat)
        m_per_deg_lat = 111132.92 - 559.82 * math.cos(2 * lat_rad) + 1.175 * math.cos(4 * lat_rad)
        m_per_deg_lon = 111412.84 * math.cos(lat_rad) - 93.5 * math.cos(3 * lat_rad)
        dx_m = pixel_x * m_per_deg_lon
        dy_m = pixel_y * m_per_deg_lat
        return max(dx_m * dy_m, 1.0)
    else:
        # Projected CRS where units are assumed to be meters
        return max(pixel_x * pixel_y, 1.0)


def extract_spill_geometries(
    mask: np.ndarray,
    probability: np.ndarray,
    raster: np.ndarray,
    transform: Affine,
    crs: CRS | None,
    background_mean_db: float,
    min_area_m2: float = 25000.0,
    max_area_m2: float | None = 250_000_000.0,
    min_pixels: int = 10,
    detector_metadata: dict[str, Any] | None = None,
) -> SpillDetectionResult:
    """Extract vector polygons and physical statistics for connected spill candidate regions.

    Args:
        mask: 2D boolean array (True = spill candidate pixel).
        probability: 2D float32 array in [0.0, 1.0].
        raster: 2D float32 calibrated sigma0 dB SAR raster.
        transform: Affine transform mapping pixel coords to CRS coords.
        crs: Coordinate reference system (e.g. EPSG:4326).
        background_mean_db: Mean ambient sea backscatter (dB) for damping contrast calculation.
        min_area_m2: Minimum region area in m2 to retain (filters sub-resolution speckle).
        max_area_m2: Maximum region area in m2 (optional upper bound for calm sea rejection).
        min_pixels: Minimum pixel count per connected component.
        detector_metadata: Extra diagnostics passed from the detection algorithm.

    Returns:
        SpillDetectionResult with detected status, geometries, areas, and confidence.
    """
    if mask.shape != probability.shape or mask.shape != raster.shape:
        raise SpillDetectionError("Shape mismatch between mask, probability, and raster arrays")

    detector_meta = detector_metadata or {}

    # Check for empty mask
    if not np.any(mask):
        return SpillDetectionResult(
            detected=False,
            confidence=0.0,
            total_area_m2=0.0,
            spill_count=0,
            geometry={"type": "GeometryCollection", "geometries": []},
            bbox=None,
            centroid=None,
            regions=[],
            raster_stats={
                "detected_pixels": 0,
                "background_mean_db": background_mean_db,
            },
            detector_metadata=detector_meta,
        )

    # 1. Label connected candidate components
    labeled, num_features = _label_connected_components(mask)
    if num_features == 0:
        return SpillDetectionResult(
            detected=False,
            confidence=0.0,
            total_area_m2=0.0,
            spill_count=0,
            geometry={"type": "GeometryCollection", "geometries": []},
            bbox=None,
            centroid=None,
            regions=[],
            raster_stats={"detected_pixels": 0, "background_mean_db": background_mean_db},
            detector_metadata=detector_meta,
        )

    # 2. Extract vector geometries and filter regions by size
    surviving_regions: list[SpillRegionStats] = []
    region_id_counter = 1

    for feature_idx in range(1, num_features + 1):
        reg_mask = labeled == feature_idx
        pixel_count = int(np.sum(reg_mask))

        if pixel_count < min_pixels:
            continue

        # Extract polygon vectors using rasterio.features.shapes
        shapes = list(
            rasterio.features.shapes(
                reg_mask.astype(np.uint8),
                mask=reg_mask,
                transform=transform,
            )
        )
        if not shapes:
            continue

        # Take the primary polygon shape
        geom_dict, _ = shapes[0]
        coords = geom_dict.get("coordinates", [])
        if not coords or not coords[0]:
            continue

        exterior = coords[0]
        lons = [pt[0] for pt in exterior]
        lats = [pt[1] for pt in exterior]

        min_lon, max_lon = min(lons), max(lons)
        min_lat, max_lat = min(lats), max(lats)
        center_lat = (min_lat + max_lat) / 2.0
        center_lon = (min_lon + max_lon) / 2.0

        # Physical ground area calculation
        pixel_area = compute_pixel_area_m2(transform, crs, center_lat=center_lat)
        area_m2 = float(pixel_count * pixel_area)
        area_km2 = float(area_m2 / 1e6)

        # Apply minimum / maximum area filters
        if area_m2 < min_area_m2:
            continue
        if max_area_m2 is not None and area_m2 > max_area_m2:
            continue

        # Raster statistics within region
        slick_vals = raster[reg_mask]
        valid_vals = slick_vals[np.isfinite(slick_vals)]
        if len(valid_vals) > 0:
            min_db = float(np.min(valid_vals))
            mean_db = float(np.mean(valid_vals))
        else:
            min_db = mean_db = float("nan")

        damping_contrast = float(background_mean_db - mean_db) if not math.isnan(mean_db) else 0.0

        # Region confidence score: mean probability weighted by damping
        reg_probs = probability[reg_mask]
        mean_prob = float(np.mean(reg_probs)) if len(reg_probs) > 0 else 0.5
        reg_confidence = float(np.clip(mean_prob, 0.0, 1.0))

        # Bounding box in WGS84 [west, south, east, north]
        bbox = (float(min_lon), float(min_lat), float(max_lon), float(max_lat))
        centroid = (float(center_lon), float(center_lat))

        surviving_regions.append(
            SpillRegionStats(
                region_id=region_id_counter,
                pixel_count=pixel_count,
                area_m2=round(area_m2, 2),
                area_km2=round(area_km2, 4),
                min_db=round(min_db, 2),
                mean_db=round(mean_db, 2),
                background_mean_db=round(background_mean_db, 2),
                damping_contrast_db=round(damping_contrast, 2),
                confidence=round(reg_confidence, 4),
                bbox=bbox,
                centroid=centroid,
                geometry=geom_dict,
            )
        )
        region_id_counter += 1

    # 3. Aggregate overall scene results
    if not surviving_regions:
        return SpillDetectionResult(
            detected=False,
            confidence=0.0,
            total_area_m2=0.0,
            spill_count=0,
            geometry={"type": "GeometryCollection", "geometries": []},
            bbox=None,
            centroid=None,
            regions=[],
            raster_stats={
                "detected_pixels": 0,
                "background_mean_db": background_mean_db,
                "filtered_out_count": num_features,
            },
            detector_metadata=detector_meta,
        )

    total_area_m2 = sum(r.area_m2 for r in surviving_regions)
    total_pixels = sum(r.pixel_count for r in surviving_regions)

    # Area-weighted confidence
    if total_area_m2 > 0:
        overall_confidence = float(
            sum(r.confidence * r.area_m2 for r in surviving_regions) / total_area_m2
        )
        # Area-weighted centroid
        weighted_lon = sum(r.centroid[0] * r.area_m2 for r in surviving_regions) / total_area_m2
        weighted_lat = sum(r.centroid[1] * r.area_m2 for r in surviving_regions) / total_area_m2
        overall_centroid = (float(weighted_lon), float(weighted_lat))
    else:
        overall_confidence = surviving_regions[0].confidence
        overall_centroid = surviving_regions[0].centroid

    # Combined bounding box
    overall_bbox = (
        min(r.bbox[0] for r in surviving_regions),
        min(r.bbox[1] for r in surviving_regions),
        max(r.bbox[2] for r in surviving_regions),
        max(r.bbox[3] for r in surviving_regions),
    )

    # Combined GeoJSON geometry: Polygon if single region, MultiPolygon if multiple
    if len(surviving_regions) == 1:
        combined_geometry = surviving_regions[0].geometry
    else:
        combined_geometry = {
            "type": "MultiPolygon",
            "coordinates": [r.geometry["coordinates"] for r in surviving_regions],
        }

    overall_min_db = min(r.min_db for r in surviving_regions)
    overall_mean_db = float(
        sum(r.mean_db * r.pixel_count for r in surviving_regions) / total_pixels
    )
    overall_damping = float(background_mean_db - overall_mean_db)

    raster_stats = {
        "detected_pixels": total_pixels,
        "spill_count": len(surviving_regions),
        "total_area_m2": round(total_area_m2, 2),
        "total_area_km2": round(total_area_m2 / 1e6, 4),
        "min_slick_db": round(overall_min_db, 2),
        "mean_slick_db": round(overall_mean_db, 2),
        "background_mean_db": round(background_mean_db, 2),
        "damping_contrast_db": round(overall_damping, 2),
    }

    return SpillDetectionResult(
        detected=True,
        confidence=round(np.clip(overall_confidence, 0.0, 1.0), 4),
        total_area_m2=round(total_area_m2, 2),
        spill_count=len(surviving_regions),
        geometry=combined_geometry,
        bbox=overall_bbox,
        centroid=overall_centroid,
        regions=surviving_regions,
        raster_stats=raster_stats,
        detector_metadata=detector_meta,
    )


def to_geojson_feature_collection(result: SpillDetectionResult) -> dict[str, Any]:
    """Convert SpillDetectionResult into standard GeoJSON FeatureCollection dictionary."""
    features = []
    for r in result.regions:
        features.append(
            {
                "type": "Feature",
                "geometry": r.geometry,
                "properties": {
                    "region_id": r.region_id,
                    "area_m2": r.area_m2,
                    "area_km2": r.area_km2,
                    "pixel_count": r.pixel_count,
                    "confidence": r.confidence,
                    "mean_db": r.mean_db,
                    "min_db": r.min_db,
                    "damping_contrast_db": r.damping_contrast_db,
                    "background_mean_db": r.background_mean_db,
                    "bbox": list(r.bbox),
                    "centroid": list(r.centroid),
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "detected": result.detected,
            "confidence": result.confidence,
            "spill_count": result.spill_count,
            "total_area_m2": result.total_area_m2,
            "total_area_km2": round(result.total_area_m2 / 1e6, 4) if result.total_area_m2 else 0.0,
            "bbox": list(result.bbox) if result.bbox else None,
            "centroid": list(result.centroid) if result.centroid else None,
            "raster_stats": result.raster_stats,
            "detector_metadata": result.detector_metadata,
        },
    }
