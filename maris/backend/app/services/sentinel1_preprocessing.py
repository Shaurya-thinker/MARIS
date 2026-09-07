"""Sentinel-1 SAR Preprocessing Service for Stage B2.

Converts a raw Sentinel-1 GRD ZIP artifact into an analysis-ready
calibrated SAR GeoTIFF (sigma0 in dB) preserving geospatial metadata.

Read-only with respect to the source Sentinel-1 ZIP artifact.
Does NOT perform model-specific ML normalization, AI spill detection,
spill segmentation, drift modeling, or frontend integration.
"""

from __future__ import annotations

from datetime import datetime, timezone

from io import BytesIO
import math
from pathlib import Path
import re
from typing import Any, NamedTuple
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import rasterio
from rasterio.crs import CRS

from app.acquisition.registry import AssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.core.config import settings
from app.models.asset import Asset
from app.models.common import AssetType, Provenance
from app.models.satellite import SatelliteScene

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


class Sentinel1PreprocessingError(Exception):
    """Raised when Sentinel-1 SAR preprocessing fails due to missing, corrupt, or malformed data."""


class CalibrationVector(NamedTuple):
    line: int
    pixels: np.ndarray
    sigma_nought: np.ndarray


def parse_calibration_xml(xml_bytes: bytes) -> tuple[str, list[CalibrationVector]]:
    """Parse Sentinel-1 annotation calibration XML into polarization and calibration vectors."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise Sentinel1PreprocessingError(f"Calibration XML is not valid XML: {exc}") from exc

    # Extract polarization attribute/element
    pol_el = root.find(".//polarisation")
    if pol_el is None or not pol_el.text:
        # Check alternative XML tag
        pol_el = root.find(".//adsHeader/polarisation")
    polarisation = pol_el.text.strip().upper() if pol_el is not None and pol_el.text else "UNKNOWN"

    vector_nodes = root.findall(".//calibrationVector")
    if not vector_nodes:
        raise Sentinel1PreprocessingError("Calibration XML contains no calibrationVector elements")

    vectors: list[CalibrationVector] = []
    for vec in vector_nodes:
        line_el = vec.find("line")
        pixel_el = vec.find("pixel")
        sigma_el = vec.find("sigmaNought")

        if line_el is None or pixel_el is None or sigma_el is None:
            continue
        if not line_el.text or not pixel_el.text or not sigma_el.text:
            continue

        try:
            line = int(line_el.text.strip())
            pixels = np.fromstring(pixel_el.text.strip(), sep=" ", dtype=np.float64)
            sigma_nought = np.fromstring(sigma_el.text.strip(), sep=" ", dtype=np.float64)
        except Exception as exc:
            raise Sentinel1PreprocessingError(f"Malformed calibration vector data: {exc}") from exc

        if len(pixels) != len(sigma_nought) or len(pixels) == 0:
            raise Sentinel1PreprocessingError("Calibration vector pixel and sigmaNought length mismatch")

        vectors.append(CalibrationVector(line=line, pixels=pixels, sigma_nought=sigma_nought))

    if not vectors:
        raise Sentinel1PreprocessingError("No valid calibration vectors extracted from XML")

    return polarisation, vectors


def build_calibration_lut_1d(vectors: list[CalibrationVector], image_width: int) -> np.ndarray:
    """Construct a 1D column calibration LUT vector of length image_width."""
    if not vectors:
        raise Sentinel1PreprocessingError("Cannot build LUT without calibration vectors")

    # Use first calibration vector for column-wise interpolation across image width
    vec = vectors[0]
    pixels = vec.pixels
    sigma = vec.sigma_nought

    if len(pixels) == 1:
        # Constant gain value
        return np.full(image_width, sigma[0], dtype=np.float64)

    # 1D interpolation across pixel column indices [0 .. image_width - 1]
    cols = np.arange(image_width, dtype=np.float64)
    lut_1d = np.interp(cols, pixels, sigma)
    return lut_1d


def compute_sigma0_db(dn_array: np.ndarray, lut_1d: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """Calibrate Digital Numbers to sigma0 linear and convert to sigma0 dB (10 * log10(sigma0)).

    sigma0_linear = (DN / A_sigma)^2
    sigma0_db = 10 * log10(sigma0_linear)

    Invalid, zero, non-positive, or non-finite values become NaN in output float32 raster.
    """
    height, width = dn_array.shape
    if lut_1d.shape[0] != width:
        raise Sentinel1PreprocessingError(
            f"Calibration LUT width {lut_1d.shape[0]} does not match image width {width}"
        )

    with np.errstate(divide="ignore", invalid="ignore"):
        dn_float = dn_array.astype(np.float64)
        lut_2d = lut_1d[np.newaxis, :]

        # Valid mask: positive finite DN and positive finite LUT gain
        valid_mask = (dn_float > 0) & (lut_2d > 0) & np.isfinite(dn_float) & np.isfinite(lut_2d)

        sigma0_linear = np.where(valid_mask, (dn_float / lut_2d) ** 2, np.nan)

        db_mask = valid_mask & np.isfinite(sigma0_linear) & (sigma0_linear > 0)
        sigma0_db = np.where(db_mask, 10.0 * np.log10(sigma0_linear), np.nan).astype(np.float32)

    invalid_count = int(np.sum(~db_mask))
    finite_count = int(np.sum(db_mask))

    if finite_count > 0:
        valid_vals = sigma0_db[db_mask]
        min_db = float(np.min(valid_vals))
        max_db = float(np.max(valid_vals))
        mean_db = float(np.mean(valid_vals))
        std_db = float(np.std(valid_vals))
    else:
        min_db = max_db = mean_db = std_db = float("nan")

    stats = {
        "invalid_pixel_count": invalid_count,
        "finite_pixel_count": finite_count,
        "min_db": min_db,
        "max_db": max_db,
        "mean_db": mean_db,
        "std_db": std_db,
    }
    return sigma0_db, stats


def preprocess_sentinel1_scene(
    investigation_id: str,
    scene: SatelliteScene,
    asset: Asset,
    output_dir: str | Path | None = None,
    registry: AssetRegistry | None = None,
) -> tuple[Asset, dict[str, Any]]:
    """Preprocess a raw Sentinel-1 GRD scene asset into an analysis-ready SAR GeoTIFF (sigma0 dB).

    Read-only with respect to the source Sentinel-1 ZIP file.
    Outputs a new derived GeoTIFF raster preserving geospatial metadata (CRS, transform, bounds).
    Registers the derived product as an Asset in AssetRegistry.
    """
    zip_path = Path(asset.location)
    if not zip_path.exists():
        raise Sentinel1PreprocessingError(f"Source Sentinel-1 ZIP file does not exist: {zip_path}")
    if not zip_path.is_file():
        raise Sentinel1PreprocessingError(f"Source Sentinel-1 ZIP path is not a file: {zip_path}")

    try:
        zf = zipfile.ZipFile(zip_path, "r")
    except zipfile.BadZipFile as exc:
        raise Sentinel1PreprocessingError(f"Source ZIP is corrupt: {exc}") from exc
    except OSError as exc:
        raise Sentinel1PreprocessingError(f"Source ZIP cannot be opened: {exc}") from exc

    with zf:
        namelist = zf.namelist()

        # Locate measurement GeoTIFF rasters
        measurement_entries = [
            n for n in namelist if "/measurement/" in n and (n.lower().endswith(".tiff") or n.lower().endswith(".tif"))
        ]
        if not measurement_entries:
            raise Sentinel1PreprocessingError("No measurement GeoTIFF rasters found in Sentinel-1 product package")

        # Locate annotation calibration XML files
        calibration_entries = [
            n for n in namelist if "/annotation/calibration/" in n and n.lower().endswith(".xml")
        ]
        if not calibration_entries:
            raise Sentinel1PreprocessingError("No annotation calibration XML files found in Sentinel-1 product package")

        # Process each measurement raster and match calibration LUT
        band_arrays: list[np.ndarray] = []
        output_pols: list[str] = []
        source_files: list[str] = []
        combined_stats: list[dict[str, Any]] = []

        geo_crs: CRS | None = None
        geo_transform = None
        geo_width: int | None = None
        geo_height: int | None = None
        geo_bounds = None

        # Sort entries deterministically (VV first if available)
        measurement_entries.sort(key=lambda name: (0 if "-vv-" in name.lower() else 1, name))

        for entry_name in measurement_entries:
            # Extract polarization from filename (e.g. s1a-iw-grd-vv-...)
            fname_lower = Path(entry_name).name.lower()
            pol_match = re.search(r"-(vv|vh|hh|hv)-", fname_lower)
            pol_from_name = pol_match.group(1).upper() if pol_match else None

            # Find matching calibration XML
            matched_cal_entry = None
            if pol_from_name:
                for cal in calibration_entries:
                    if f"-{pol_from_name.lower()}-" in cal.lower():
                        matched_cal_entry = cal
                        break
            if not matched_cal_entry:
                matched_cal_entry = calibration_entries[0]

            # Read measurement GeoTIFF bytes
            try:
                tiff_bytes = zf.read(entry_name)
            except Exception as exc:
                raise Sentinel1PreprocessingError(f"Failed to read measurement entry '{entry_name}': {exc}") from exc

            # Read calibration XML bytes
            try:
                cal_bytes = zf.read(matched_cal_entry)
            except Exception as exc:
                raise Sentinel1PreprocessingError(f"Failed to read calibration entry '{matched_cal_entry}': {exc}") from exc

            cal_pol, cal_vectors = parse_calibration_xml(cal_bytes)
            actual_pol = pol_from_name or cal_pol

            with rasterio.open(BytesIO(tiff_bytes)) as src:
                dn_array = src.read(1)
                if geo_crs is None:
                    geo_crs = src.crs or CRS.from_epsg(4326)
                    geo_transform = src.transform
                    geo_width = src.width
                    geo_height = src.height
                    geo_bounds = src.bounds

            lut_1d = build_calibration_lut_1d(cal_vectors, dn_array.shape[1])
            sigma0_db, stats = compute_sigma0_db(dn_array, lut_1d)

            band_arrays.append(sigma0_db)
            output_pols.append(actual_pol)
            source_files.append(Path(entry_name).name)
            combined_stats.append(stats)

    # Validate output spatial bounds
    assert geo_width is not None and geo_height is not None
    assert geo_transform is not None and geo_bounds is not None

    # Construct output derived raster path
    if output_dir is not None:
        target_dir = Path(output_dir)
    else:
        safe_inv = _UNSAFE_FILENAME.sub("_", investigation_id).strip("._") or "investigation"
        safe_scene = _UNSAFE_FILENAME.sub("_", scene.id).strip("._") or "scene"
        target_dir = Path(settings.data_dir) / "derived" / safe_inv / "sar" / safe_scene

    target_dir.mkdir(parents=True, exist_ok=True)
    derived_raster_path = target_dir / "sentinel1_sigma0_db.tif"

    # Write derived multi-band GeoTIFF raster using rasterio
    num_bands = len(band_arrays)
    with rasterio.open(
        derived_raster_path,
        "w",
        driver="GTiff",
        height=geo_height,
        width=geo_width,
        count=num_bands,
        dtype=np.float32,
        crs=geo_crs,
        transform=geo_transform,
        nodata=np.nan,
    ) as dst:
        for idx, (arr, pol) in enumerate(zip(band_arrays, output_pols), start=1):
            dst.write(arr.astype(np.float32), idx)
            dst.set_band_description(idx, f"sigma0_db_{pol}")

    # Calculate overall statistics across bands
    total_invalid = sum(s["invalid_pixel_count"] for s in combined_stats)
    total_finite = sum(s["finite_pixel_count"] for s in combined_stats)

    valid_mins = [s["min_db"] for s in combined_stats if not math.isnan(s["min_db"])]
    valid_maxs = [s["max_db"] for s in combined_stats if not math.isnan(s["max_db"])]
    valid_means = [s["mean_db"] for s in combined_stats if not math.isnan(s["mean_db"])]

    overall_min = min(valid_mins) if valid_mins else float("nan")
    overall_max = max(valid_maxs) if valid_maxs else float("nan")
    overall_mean = float(np.mean(valid_means)) if valid_means else float("nan")

    # Assemble derived Asset metadata
    pixel_size_x = abs(geo_transform.a)
    pixel_size_y = abs(geo_transform.e)

    derived_metadata: dict[str, Any] = {
        "parent_asset_id": asset.id,
        "parent_scene_id": scene.id,
        "source_product_name": scene.metadata.get("product_name") or zip_path.name,
        "source_product_id": asset.provenance.product_id,
        "sensor": scene.sensor,
        "mode": scene.metadata.get("sensor_mode"),
        "product_type": scene.metadata.get("product_type"),
        "polarizations": output_pols,
        "calibration": "sigma0_db",
        "output_units": "dB",
        "width": geo_width,
        "height": geo_height,
        "crs": str(geo_crs),
        "pixel_size": [pixel_size_x, pixel_size_y],
        "bounds": {
            "west": geo_bounds.left,
            "south": geo_bounds.bottom,
            "east": geo_bounds.right,
            "north": geo_bounds.top,
        },
        "source_measurement_files": source_files,
        "preprocessing_steps": [
            "measurement_raster_reading",
            "radiometric_calibration_sigma0",
            "decibel_conversion_10log10",
            "invalid_pixel_masking",
            "georeferenced_geotiff_export",
        ],
        "invalid_pixel_count": total_invalid,
        "finite_pixel_count": total_finite,
        "min_db": overall_min,
        "max_db": overall_max,
        "mean_db": overall_mean,
        "std_db": float(np.std([s["std_db"] for s in combined_stats if not math.isnan(s["std_db"])]))
        if valid_means
        else float("nan"),
    }

    # Register derived Asset in AssetRegistry
    retrieved_at = datetime.now(timezone.utc)
    derived_artifact = AcquiredArtifact(
        asset_type=AssetType.IMAGERY_PREVIEW,
        location=str(derived_raster_path.resolve()),
        source="sentinel1_preprocessing_service",
        acquisition_time=scene.acquisition_time,
        provenance=Provenance(
            product_id=asset.provenance.product_id or zip_path.name,
            retrieved_at=retrieved_at,
            processing_level="calibrated_sigma0_db",
            notes="Analysis-ready calibrated SAR GeoTIFF (sigma0 dB) produced in MARIS Stage B2.",
            extra={
                "parent_asset_id": asset.id,
                "parent_scene_id": scene.id,
                "calibration": "sigma0_db",
                "polarizations": output_pols,
            },
        ),
        metadata=derived_metadata,
    )

    target_registry = registry or default_asset_registry
    derived_asset = target_registry.register(investigation_id, asset.provider, derived_artifact)

    return derived_asset, derived_metadata
