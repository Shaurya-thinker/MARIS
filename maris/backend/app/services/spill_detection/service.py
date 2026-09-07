"""Spill detection and geometry derivation service for Stage B3.

Consumes analysis-ready calibrated Sentinel-1 SAR GeoTIFF rasters (sigma0 in dB)
produced by Stage B2, applies modular detection algorithms (such as the deterministic
adaptive local thresholding baseline), cleans candidate masks, extracts vector geometries,
and registers derived artifacts with full provenance.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from typing import Any

import numpy as np
import rasterio
from rasterio.crs import CRS

from app.acquisition.registry import AssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.core.config import settings
from app.models.asset import Asset
from app.models.common import AssetType, Provenance
from app.models.satellite import SatelliteScene, SpillDetection
from app.services.spill_detection.base import (
    BaseSpillDetector,
    SpillDetectionError,
    SpillDetectionResult,
)
from app.services.spill_detection.detector import AdaptiveThresholdSpillDetector
from app.services.spill_detection.geometry import (
    compute_pixel_area_m2,
    extract_spill_geometries,
    to_geojson_feature_collection,
)

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize_path_segment(val: str, fallback: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub("_", val).strip("._")
    return cleaned or fallback


def select_polarization_band(
    src: rasterio.io.DatasetReader,
    requested_pol: str | None = None,
) -> tuple[int, str]:
    """Select appropriate raster band based on requested polarization or standard priority (VV > VH > band 1).

    Returns:
        (band_index_1_based, polarization_str)
    """
    count = src.count
    descriptions = list(src.descriptions or [])

    # Map available band descriptions to polarization names
    band_pols: list[str] = []
    for idx in range(count):
        desc = descriptions[idx] if idx < len(descriptions) and descriptions[idx] else ""
        desc_upper = desc.upper()
        if "VV" in desc_upper:
            band_pols.append("VV")
        elif "VH" in desc_upper:
            band_pols.append("VH")
        elif "HH" in desc_upper:
            band_pols.append("HH")
        elif "HV" in desc_upper:
            band_pols.append("HV")
        else:
            band_pols.append(f"BAND_{idx+1}")

    if requested_pol is not None:
        req_clean = requested_pol.strip().upper()
        for idx, pol in enumerate(band_pols):
            if pol == req_clean:
                return idx + 1, pol
        raise SpillDetectionError(
            f"Requested polarization '{requested_pol}' not found in raster bands: {band_pols}"
        )

    # Automatic selection priority: VV first (highest ocean SCR), then VH, then band 1
    for idx, pol in enumerate(band_pols):
        if pol == "VV":
            return idx + 1, pol

    for idx, pol in enumerate(band_pols):
        if pol == "VH":
            return idx + 1, pol

    return 1, band_pols[0] if band_pols else "UNKNOWN"


def detect_spills_from_sar_scene(
    investigation_id: str,
    scene: SatelliteScene,
    sar_asset: Asset,
    detector: BaseSpillDetector | None = None,
    polarization: str | None = None,
    min_area_m2: float = 25000.0,
    max_area_m2: float | None = 250_000_000.0,
    min_pixels: int = 10,
    output_dir: str | Path | None = None,
    registry: AssetRegistry | None = None,
) -> tuple[SpillDetection, Asset]:
    """Run the Stage B3 spill detection and geometry pipeline on a calibrated SAR GeoTIFF.

    1. Validates and opens B2 calibrated SAR GeoTIFF in read-only mode (source unchanged).
    2. Identifies and selects appropriate polarization band (VV prioritized, VH separate).
    3. Executes the spill detector (defaults to AdaptiveThresholdSpillDetector).
    4. Performs connected-component analysis and extracts geospatial vector geometries.
    5. Saves deterministic output artifacts (GeoTIFF mask and GeoJSON vector).
    6. Assembles and registers derived Asset in AssetRegistry.
    7. Returns domain SpillDetection object and registered Asset.
    """
    source_path = Path(sar_asset.location)
    if not source_path.exists():
        raise SpillDetectionError(f"Source SAR GeoTIFF raster does not exist: {source_path}")
    if not source_path.is_file():
        raise SpillDetectionError(f"Source SAR raster path is not a file: {source_path}")

    # Use default adaptive threshold detector if none provided
    active_detector = detector or AdaptiveThresholdSpillDetector()

    # Read-only raster consumption
    try:
        with rasterio.open(source_path, "r") as src:
            geo_crs = src.crs or CRS.from_epsg(4326)
            geo_transform = src.transform
            geo_width = src.width
            geo_height = src.height
            geo_nodata = src.nodata

            band_index, chosen_pol = select_polarization_band(src, requested_pol=polarization)
            raster_band = src.read(band_index)
    except SpillDetectionError:
        raise
    except Exception as exc:
        raise SpillDetectionError(f"Failed to read source SAR GeoTIFF '{source_path}': {exc}") from exc

    # Valid mask: finite, not nodata, above sensor noise floor
    valid_mask = np.isfinite(raster_band)
    if geo_nodata is not None and not math.isnan(geo_nodata):
        valid_mask = valid_mask & (raster_band != geo_nodata)

    # Pixel resolution in ground meters
    dx_deg = abs(geo_transform.a)
    dy_deg = abs(geo_transform.e)
    center_lat = float(scene.footprint.coordinates[0][0][1]) if hasattr(scene.footprint, "coordinates") else 0.0
    pixel_area_m2 = compute_pixel_area_m2(geo_transform, geo_crs, center_lat=center_lat)
    pixel_size_m = (math.sqrt(pixel_area_m2), math.sqrt(pixel_area_m2))

    # Run detection
    det_result = active_detector.detect(
        raster=raster_band,
        valid_mask=valid_mask,
        polarization=chosen_pol,
        pixel_size_m=pixel_size_m,
    )

    # Background ambient sea backscatter mean
    bg_mean = det_result.metadata.get("global_background_mean_db", 0.0)
    if math.isnan(bg_mean):
        bg_mean = 0.0

    # Extract geometries and region statistics
    detection_summary: SpillDetectionResult = extract_spill_geometries(
        mask=det_result.mask,
        probability=det_result.probability,
        raster=raster_band,
        transform=geo_transform,
        crs=geo_crs,
        background_mean_db=bg_mean,
        min_area_m2=min_area_m2,
        max_area_m2=max_area_m2,
        min_pixels=min_pixels,
        detector_metadata=det_result.metadata,
    )

    # Output directory setup
    if output_dir is not None:
        target_dir = Path(output_dir)
    else:
        safe_inv = _sanitize_path_segment(investigation_id, "investigation")
        safe_scene = _sanitize_path_segment(scene.id, "scene")
        target_dir = Path(settings.data_dir) / "derived" / safe_inv / "sar" / safe_scene

    target_dir.mkdir(parents=True, exist_ok=True)
    mask_tif_path = target_dir / "spill_mask.tif"
    geom_geojson_path = target_dir / "spill_geometry.geojson"

    # Write 2-band detection GeoTIFF: Band 1 = Mask (uint8), Band 2 = Probability (float32)
    with rasterio.open(
        mask_tif_path,
        "w",
        driver="GTiff",
        height=geo_height,
        width=geo_width,
        count=2,
        dtype=np.float32,
        crs=geo_crs,
        transform=geo_transform,
        nodata=np.nan,
    ) as dst:
        dst.write(det_result.mask.astype(np.float32), 1)
        dst.set_band_description(1, f"spill_mask_{chosen_pol}")
        dst.write(det_result.probability, 2)
        dst.set_band_description(2, f"spill_probability_{chosen_pol}")

    # Write GeoJSON vector feature collection
    geojson_data = to_geojson_feature_collection(detection_summary)
    geojson_data["properties"]["investigation_id"] = investigation_id
    geojson_data["properties"]["scene_id"] = scene.id
    geojson_data["properties"]["parent_asset_id"] = sar_asset.id
    geojson_data["properties"]["polarization"] = chosen_pol

    with open(geom_geojson_path, "w", encoding="utf-8") as gf:
        json.dump(geojson_data, gf, indent=2)

    # Assemble SpillDetection metadata
    detection_id = f"spill-detect-{_sanitize_path_segment(scene.id, 'scene')}"

    bbox_dict = None
    if detection_summary.bbox is not None:
        bbox_dict = {
            "west": detection_summary.bbox[0],
            "south": detection_summary.bbox[1],
            "east": detection_summary.bbox[2],
            "north": detection_summary.bbox[3],
        }

    centroid_dict = None
    if detection_summary.centroid is not None:
        centroid_dict = {
            "longitude": detection_summary.centroid[0],
            "latitude": detection_summary.centroid[1],
        }

    derived_metadata: dict[str, Any] = {
        "parent_asset_id": sar_asset.id,
        "parent_scene_id": scene.id,
        "polarization_used": chosen_pol,
        "model_version": active_detector.model_version,
        "bounding_box": bbox_dict,
        "centroid": centroid_dict,
        "spill_count": detection_summary.spill_count,
        "raster_statistics": detection_summary.raster_stats,
        "detector_parameters": det_result.metadata.get("parameters", {}),
        "mask_raster_path": str(mask_tif_path.resolve()),
        "geometry_geojson_path": str(geom_geojson_path.resolve()),
        "regions": [
            {
                "region_id": r.region_id,
                "pixel_count": r.pixel_count,
                "area_m2": r.area_m2,
                "area_km2": r.area_km2,
                "min_db": r.min_db,
                "mean_db": r.mean_db,
                "damping_contrast_db": r.damping_contrast_db,
                "confidence": r.confidence,
                "bbox": list(r.bbox),
                "centroid": list(r.centroid),
            }
            for r in detection_summary.regions
        ],
    }

    # Register derived Asset in AssetRegistry
    retrieved_at = datetime.now(timezone.utc)
    derived_artifact = AcquiredArtifact(
        asset_type=AssetType.SPILL_GEOMETRY,
        location=str(geom_geojson_path.resolve()),
        source="sar_spill_detection_service",
        acquisition_time=scene.acquisition_time or sar_asset.acquisition_time,
        provenance=Provenance(
            product_id=sar_asset.provenance.product_id,
            retrieved_at=retrieved_at,
            processing_level="derived_spill_geometry",
            notes="Observed or derived spill candidate geometry produced in MARIS Stage B3.",
            extra={
                "parent_asset_id": sar_asset.id,
                "parent_scene_id": scene.id,
                "detector_model": active_detector.model_version,
                "polarization": chosen_pol,
                "mask_raster_path": str(mask_tif_path.resolve()),
            },
        ),
        metadata=derived_metadata,
    )

    target_registry = registry or default_asset_registry
    derived_asset = target_registry.register(investigation_id, sar_asset.provider, derived_artifact)

    # Assemble SpillDetection domain object
    spill_detection = SpillDetection(
        id=detection_id,
        investigation_id=investigation_id,
        asset_id=derived_asset.id,
        scene_id=scene.id,
        detected=detection_summary.detected,
        confidence=detection_summary.confidence if detection_summary.detected else 0.0,
        geometry=detection_summary.geometry,
        area=detection_summary.total_area_m2,
        model_version=active_detector.model_version,
        metadata=derived_metadata,
    )

    return spill_detection, derived_asset
