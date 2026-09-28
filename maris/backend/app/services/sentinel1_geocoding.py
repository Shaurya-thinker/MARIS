"""Sentinel-1 SAR Ellipsoid Geocoding Service for Stage B2.5.

Converts a native-geometry calibrated SAR GeoTIFF (sigma0 in dB) with embedded
authoritative Sentinel-1 Ground Control Points (GCPs) into an analysis-ready,
North-Up geocoded GeoTIFF (EPSG:4326) using an out-of-core Thin Plate Spline (TPS)
transformation.

Preserves established radiometric calibration semantics (DN -> sigma0 -> sigma0 dB)
and streams block-by-block to guarantee bounded memory consumption (< 250 MB RSS)
even on 437M+ pixel full scenes.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import Affine
from rasterio.warp import calculate_default_transform, reproject

from app.acquisition.registry import AssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.core.config import settings
from app.models.asset import Asset
from app.models.common import AssetType, Provenance
from app.models.satellite import SatelliteScene

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


class Sentinel1GeocodingError(Exception):
    """Raised when Sentinel-1 SAR geocoding fails due to missing, invalid, or malformed GCP/spatial metadata."""


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _get_peak_rss_mb() -> float:
    """Query current process peak working set in MB (Windows/POSIX safe)."""
    if sys.platform == "win32":
        try:
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return float(counters.PeakWorkingSetSize) / (1024.0 * 1024.0)
        except Exception:
            pass
    return float("nan")


def validate_source_raster_gcps(src: rasterio.io.DatasetReader) -> tuple[list[Any], CRS]:
    """Validate that source raster contains authoritative, geographically valid, and adequately spanning GCPs.

    Fails closed if:
    - GCPs are missing
    - Fewer than 4 GCPs exist (insufficient for 2D spatial transformation)
    - GCP CRS is absent or invalid
    - GCP coordinates are out of physical bounds (lon [-180, 180], lat [-90, 90])
    - GCP pixel/line coordinates lie outside the raster dimensions
    - GCPs fail to adequately span the spatial extent of the raster (coverage < 80%)
    """
    gcps, gcp_crs = src.gcps
    if not gcps or len(gcps) == 0:
        raise Sentinel1GeocodingError("Source raster contains no Ground Control Points (GCPs).")

    if len(gcps) < 4:
        raise Sentinel1GeocodingError(
            f"Insufficient GCP count ({len(gcps)}) for spatial geocoding transformation (minimum 4 required)."
        )

    if gcp_crs is None:
        raise Sentinel1GeocodingError("Source raster GCPs lack valid coordinate reference system (CRS) metadata.")

    # Validate coordinate values and ranges
    lons = [float(g.x) for g in gcps]
    lats = [float(g.y) for g in gcps]
    rows = [float(g.row) for g in gcps]
    cols = [float(g.col) for g in gcps]

    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)

    if min_lon < -180.0 or max_lon > 180.0 or min_lat < -90.0 or max_lat > 90.0:
        raise Sentinel1GeocodingError(
            f"Invalid GCP geographic coordinate range: lon=[{min_lon}, {max_lon}], lat=[{min_lat}, {max_lat}]."
        )

    min_row, max_row = min(rows), max(rows)
    min_col, max_col = min(cols), max(cols)

    if min_row < 0.0 or max_row > float(src.height) or min_col < 0.0 or max_col > float(src.width):
        raise Sentinel1GeocodingError(
            f"GCP pixel/line coordinates out of bounds: rows=[{min_row}, {max_row}] vs height {src.height}, "
            f"cols=[{min_col}, {max_col}] vs width {src.width}."
        )

    # Check that GCPs span the raster adequately (at least 80% coverage in both axes)
    row_span = max_row - min_row
    col_span = max_col - min_col
    if row_span < 0.8 * float(src.height) or col_span < 0.8 * float(src.width):
        raise Sentinel1GeocodingError(
            f"GCPs do not adequately span the raster domain: row span={row_span}/{src.height}, "
            f"col span={col_span}/{src.width} (< 80% coverage)."
        )

    return gcps, gcp_crs


def compute_geocoding_target_grid(
    src: rasterio.io.DatasetReader,
    gcps: list[Any],
    gcp_crs: CRS,
    target_epsg: int = 4326,
) -> tuple[Affine, int, int, CRS]:
    """Compute target North-Up affine transform, dimensions, and CRS from authoritative GCPs.

    Never falls back to an identity transform.
    Never fabricates geographic bounds.
    """
    target_crs = CRS.from_epsg(target_epsg)

    try:
        dst_transform, dst_width, dst_height = calculate_default_transform(
            src_crs=gcp_crs,
            dst_crs=target_crs,
            width=src.width,
            height=src.height,
            gcps=gcps,
        )
    except Exception as exc:
        raise Sentinel1GeocodingError(f"Failed to calculate target geocoding transform from GCPs: {exc}") from exc

    if dst_transform.is_identity:
        raise Sentinel1GeocodingError("Computed destination transform is identity matrix; geocoding failed.")

    # Strict North-Up validation: positive pixel width, negative pixel height, zero rotation/shear
    if dst_transform.a <= 0.0 or dst_transform.e >= 0.0:
        raise Sentinel1GeocodingError(
            f"Computed destination transform is not North-Up: dx={dst_transform.a}, dy={dst_transform.e}."
        )

    if abs(dst_transform.b) > 1e-9 or abs(dst_transform.d) > 1e-9:
        raise Sentinel1GeocodingError(
            f"Computed destination transform contains non-zero shear/rotation: b={dst_transform.b}, d={dst_transform.d}."
        )

    if dst_width <= 0 or dst_height <= 0:
        raise Sentinel1GeocodingError(
            f"Computed destination raster dimensions are invalid: width={dst_width}, height={dst_height}."
        )

    return dst_transform, dst_width, dst_height, target_crs


def geocode_sentinel1_raster(
    source_raster_path: str | Path,
    output_raster_path: str | Path,
    block_size: int = 2048,
    resampling: Resampling = Resampling.bilinear,
) -> dict[str, Any]:
    """Execute out-of-core block-by-block Thin Plate Spline (TPS) geocoding.

    Reads from calibrated native-geometry GeoTIFF containing authoritative GCPs.
    Streams 2048 x 2048 destination blocks to ensure peak memory stays < 250 MB RSS.
    Produces an analysis-ready North-Up GeoTIFF with DEFLATE compression and BIGTIFF where required.
    """
    src_path = Path(source_raster_path)
    dst_path = Path(output_raster_path)

    if not src_path.exists():
        raise Sentinel1GeocodingError(f"Source calibrated raster does not exist: {src_path}")
    if not src_path.is_file():
        raise Sentinel1GeocodingError(f"Source calibrated raster is not a file: {src_path}")

    dst_path.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    blocks_processed = 0
    total_blocks = 0

    with rasterio.open(src_path, "r") as src:
        # 1. Validate GCPs
        gcps, gcp_crs = validate_source_raster_gcps(src)

        # 2. Derive destination target grid
        dst_transform, dst_width, dst_height, target_crs = compute_geocoding_target_grid(
            src, gcps, gcp_crs, target_epsg=4326
        )

        num_bands = src.count
        source_descriptions = list(src.descriptions or [])
        dtypes = src.dtypes

        # Calculate uncompressed size in bytes to determine BigTIFF requirement (> 2 GiB triggers BigTIFF)
        est_uncompressed_bytes = dst_width * dst_height * num_bands * 4
        bigtiff_opt = "YES" if est_uncompressed_bytes > 2 * 1024**3 else "IF_SAFER"

        profile = {
            "driver": "GTiff",
            "height": dst_height,
            "width": dst_width,
            "count": num_bands,
            "dtype": np.float32,
            "crs": target_crs,
            "transform": dst_transform,
            "nodata": np.nan,
            "tiled": True,
            "blockxsize": block_size,
            "blockysize": block_size,
            "compress": "deflate",
            "predictor": 3,
            "bigtiff": bigtiff_opt,
        }

        # 3. Stream block-by-block out-of-core
        with rasterio.open(dst_path, "w", **profile) as dst:
            for b in range(1, num_bands + 1):
                # Set band description (e.g. sigma0_db_VV)
                desc = source_descriptions[b - 1] if b - 1 < len(source_descriptions) and source_descriptions[b - 1] else f"band_{b}"
                dst.set_band_description(b, desc)

                windows = list(dst.block_windows(b))
                if b == 1:
                    total_blocks = len(windows) * num_bands

                for _, window in windows:
                    win_transform = rasterio.windows.transform(window, dst_transform)
                    tile_buf = np.full((window.height, window.width), np.nan, dtype=np.float32)

                    reproject(
                        source=rasterio.band(src, b),
                        destination=tile_buf,
                        src_crs=gcp_crs,
                        src_nodata=src.nodata,
                        dst_transform=win_transform,
                        dst_crs=target_crs,
                        dst_nodata=np.nan,
                        resampling=resampling,
                        METHOD="GCP_TPS",
                    )

                    dst.write(tile_buf, b, window=window)
                    blocks_processed += 1

    elapsed = time.time() - t0
    peak_rss = _get_peak_rss_mb()
    file_size_bytes = dst_path.stat().st_size if dst_path.exists() else 0

    # Bounds
    bounds_tuple = rasterio.transform.array_bounds(dst_height, dst_width, dst_transform)
    bounds_dict = {
        "west": float(bounds_tuple[0]),
        "south": float(bounds_tuple[1]),
        "east": float(bounds_tuple[2]),
        "north": float(bounds_tuple[3]),
    }

    return {
        "output_path": str(dst_path.resolve()),
        "output_size_bytes": file_size_bytes,
        "width": dst_width,
        "height": dst_height,
        "count": num_bands,
        "crs": str(target_crs),
        "transform": dst_transform,
        "bounds": bounds_dict,
        "pixel_size": [abs(dst_transform.a), abs(dst_transform.e)],
        "gcp_count": len(gcps),
        "geocoding_method": "GCP_TPS",
        "block_size": block_size,
        "blocks_processed": blocks_processed,
        "total_blocks": total_blocks,
        "compression": "deflate",
        "bigtiff": bigtiff_opt,
        "elapsed_sec": elapsed,
        "peak_rss_mb": peak_rss,
    }


def geocode_sentinel1_scene(
    investigation_id: str,
    scene: SatelliteScene,
    native_sar_asset: Asset,
    output_dir: str | Path | None = None,
    registry: AssetRegistry | None = None,
    block_size: int = 2048,
) -> tuple[Asset, dict[str, Any]]:
    """Stage B2.5 pipeline orchestrator for Sentinel-1 Ellipsoid Geocoding.

    Consumes the native calibrated B2 GeoTIFF asset, validates its GCPs,
    executes out-of-core TPS geocoding, and registers the derived geocoded
    product in AssetRegistry for direct Stage B3 consumption.
    """
    native_raster_path = Path(native_sar_asset.location)
    if not native_raster_path.exists():
        raise Sentinel1GeocodingError(f"Native SAR asset raster does not exist: {native_raster_path}")

    # Determine destination directory
    if output_dir is not None:
        target_dir = Path(output_dir)
    else:
        safe_inv = _UNSAFE_FILENAME.sub("_", investigation_id).strip("._") or "investigation"
        safe_scene = _UNSAFE_FILENAME.sub("_", scene.id).strip("._") or "scene"
        target_dir = Path(settings.data_dir) / "derived" / safe_inv / "sar" / safe_scene

    target_dir.mkdir(parents=True, exist_ok=True)
    geocoded_raster_path = target_dir / "sentinel1_sigma0_db.tif"

    # Execute geocoding
    geocoding_metrics = geocode_sentinel1_raster(
        source_raster_path=native_raster_path,
        output_raster_path=geocoded_raster_path,
        block_size=block_size,
    )

    # Build derived Asset metadata
    derived_metadata: dict[str, Any] = {
        "parent_asset_id": native_sar_asset.id,
        "parent_scene_id": scene.id,
        "source_product_name": scene.metadata.get("product_name") or native_raster_path.name,
        "source_product_id": native_sar_asset.provenance.product_id,
        "sensor": scene.sensor,
        "mode": scene.metadata.get("sensor_mode"),
        "product_type": scene.metadata.get("product_type"),
        "calibration": "sigma0_db",
        "output_units": "dB",
        "georeferencing": "geocoded_ellipsoid_tps",
        "geocoding_method": "GCP_TPS",
        "gcp_count": geocoding_metrics["gcp_count"],
        "width": geocoding_metrics["width"],
        "height": geocoding_metrics["height"],
        "crs": geocoding_metrics["crs"],
        "pixel_size": geocoding_metrics["pixel_size"],
        "bounds": geocoding_metrics["bounds"],
        "block_size": geocoding_metrics["block_size"],
        "blocks_processed": geocoding_metrics["blocks_processed"],
        "compression": geocoding_metrics["compression"],
        "bigtiff": geocoding_metrics["bigtiff"],
        "elapsed_sec": geocoding_metrics["elapsed_sec"],
        "peak_rss_mb": geocoding_metrics["peak_rss_mb"],
    }

    retrieved_at = datetime.now(timezone.utc)
    derived_artifact = AcquiredArtifact(
        asset_type=AssetType.IMAGERY_PREVIEW,
        location=str(geocoded_raster_path.resolve()),
        source="sentinel1_geocoding_service",
        acquisition_time=scene.acquisition_time,
        provenance=Provenance(
            product_id=native_sar_asset.provenance.product_id or native_raster_path.name,
            retrieved_at=retrieved_at,
            processing_level="geocoded_sigma0_db",
            notes="Analysis-ready North-Up geocoded SAR GeoTIFF (sigma0 dB) produced in MARIS Stage B2.5 via GCP TPS.",
            extra={
                "parent_asset_id": native_sar_asset.id,
                "parent_scene_id": scene.id,
                "calibration": "sigma0_db",
                "geocoding_method": "GCP_TPS",
            },
        ),
        metadata=derived_metadata,
    )

    target_registry = registry or default_asset_registry
    geocoded_asset = target_registry.register(investigation_id, native_sar_asset.provider, derived_artifact)

    return geocoded_asset, derived_metadata
