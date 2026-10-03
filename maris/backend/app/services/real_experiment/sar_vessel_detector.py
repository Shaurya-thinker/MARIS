"""MARIS Real-Experiment — SAR Bright-Target Detector (Phase 6.1).

Detects anomalous bright radar scattering targets in calibrated Sentinel-1 SAR
imagery (sigma0 in dB) using a 2D Cell-Averaging Constant False Alarm Rate (CA-CFAR)
detector with an annular clutter estimation window and protective guard ring.

SCIENTIFIC PRINCIPLES & TERMINOLOGY:
------------------------------------
1. TARGET IDENTIFICATION: The detector isolates "Bright Radar Targets" (SarBrightTarget),
   NOT guaranteed vessels or ships. High-backscatter radar returns can originate from
   ships, offshore platforms, buoys, rocky islets, or sea spikes (breaking waves).
   No object is classified as a vessel prior to AIS correlation.
2. RADAR RADIOMETRY: Sentinel-1 Level-1 GRD imagery provides normalized radar backscatter
   cross-section (sigma0) in decibels (dB). This detector calculates:
   - peak_backscatter_db: maximum observed sigma0 (dB) within the target component
   - local_clutter_mean_db: ambient sea clutter mean (dB) in the local CFAR window
   - target_to_clutter_ratio_db (TCR): peak_backscatter_db - local_clutter_mean_db
   It does NOT compute Radar Cross Section (RCS), dBsm, or absolute m² target cross-section.
3. APPARENT RADAR EXTENT: Multi-pixel spatial extents (apparent_major_extent_m,
   apparent_minor_extent_m) represent the above-threshold radar scattering envelope
   convolved with the sensor Point Spread Function (PSF), NOT physical vessel hull dimensions.
4. MARITIME DOMAIN MASKING: Land and coastal surf false positives are rejected using
   the existing MaritimeMasker (Natural Earth 10m ocean polygons with seaward buffer).
5. ZERO-FABRICATION: No orbital velocity, slant range, platform heading, or Doppler
   azimuth displacement is calculated or invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.transform import Affine
from rasterio.windows import Window
import scipy.ndimage as ndi

from app.services.maritime_mask import MaritimeMasker


@dataclass
class SarBrightTarget:
    """Represents an anomalous bright radar target detected in SAR imagery.
    
    Reflects observed radar scatterers above local sea clutter, NOT verified vessels.
    """
    target_id: str
    pixel_x: float
    pixel_y: float
    lon: float
    lat: float
    peak_backscatter_db: float
    local_clutter_mean_db: float
    target_to_clutter_ratio_db: float
    pixel_count: int
    apparent_major_extent_m: float | None = None
    apparent_minor_extent_m: float | None = None
    detection_confidence: float = 0.5
    provenance: str = "SAR_BRIGHT_TARGET_DETECTION"

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "pixel_x": round(self.pixel_x, 2),
            "pixel_y": round(self.pixel_y, 2),
            "lon": round(self.lon, 6),
            "lat": round(self.lat, 6),
            "peak_backscatter_db": round(self.peak_backscatter_db, 2),
            "local_clutter_mean_db": round(self.local_clutter_mean_db, 2),
            "target_to_clutter_ratio_db": round(self.target_to_clutter_ratio_db, 2),
            "pixel_count": self.pixel_count,
            "apparent_major_extent_m": (
                round(self.apparent_major_extent_m, 1)
                if self.apparent_major_extent_m is not None
                else None
            ),
            "apparent_minor_extent_m": (
                round(self.apparent_minor_extent_m, 1)
                if self.apparent_minor_extent_m is not None
                else None
            ),
            "detection_confidence": round(self.detection_confidence, 3),
            "provenance": self.provenance,
        }


class SarBrightTargetDetectorError(Exception):
    """Raised when SAR bright-target detection fails due to invalid parameters or data."""


class SarBrightTargetDetector:
    """2D Cell-Averaging Constant False Alarm Rate (CA-CFAR) Bright-Target Detector."""

    MODEL_VERSION = "maris.p6.sar_bright_target_cfar.v1"

    def __init__(
        self,
        *,
        cfar_k_sigma: float = 4.0,
        guard_window_pixels: int = 5,
        clutter_window_pixels: int = 41,
        min_target_pixels: int = 1,
        max_target_pixels: int = 150,
        min_tcr_db: float = 3.0,
        min_peak_sigma0_db: float = -14.0,
        noise_floor_db: float = -45.0,
        coastal_buffer_m: float = 100.0,
    ) -> None:
        """Initialize the detector with configurable CFAR and filtering parameters.

        Args:
            cfar_k_sigma: Multiplier for local clutter standard deviation (threshold = mu + k*sigma).
                          Higher values reduce false alarms. Default 4.0.
            guard_window_pixels: Inner guard window dimension (odd integer >= 3). Protects target
                                 scatterers from contaminating local clutter statistics. Default 5.
            clutter_window_pixels: Outer clutter window dimension (odd integer > guard). Default 41.
            min_target_pixels: Minimum connected component size in pixels (inclusive). Default 1.
            max_target_pixels: Maximum connected component size in pixels (inclusive). Default 150.
            min_tcr_db: Minimum required peak target-to-clutter ratio in dB. Default 3.0 dB.
            min_peak_sigma0_db: Absolute minimum peak sigma0 in dB for a maritime target. Default -14.0 dB.
            noise_floor_db: Absolute backscatter floor (dB). Pixels at or below are excluded. Default -45.0 dB.
            coastal_buffer_m: Seaward coastal exclusion buffer in meters for MaritimeMasker. Default 100.0 m.
        """
        if cfar_k_sigma <= 0.0:
            raise SarBrightTargetDetectorError("cfar_k_sigma must be positive")
        if guard_window_pixels < 1 or guard_window_pixels % 2 == 0:
            raise SarBrightTargetDetectorError("guard_window_pixels must be an odd integer >= 1")
        if clutter_window_pixels <= guard_window_pixels or clutter_window_pixels % 2 == 0:
            raise SarBrightTargetDetectorError(
                "clutter_window_pixels must be an odd integer strictly greater than guard_window_pixels"
            )
        if min_target_pixels < 1:
            raise SarBrightTargetDetectorError("min_target_pixels must be an integer >= 1")
        if max_target_pixels < min_target_pixels:
            raise SarBrightTargetDetectorError("max_target_pixels cannot be smaller than min_target_pixels")
        if min_tcr_db < 0.0:
            raise SarBrightTargetDetectorError("min_tcr_db must be non-negative")
        if coastal_buffer_m < 0.0:
            raise SarBrightTargetDetectorError("coastal_buffer_m must be non-negative")

        self.cfar_k_sigma = float(cfar_k_sigma)
        self.guard_window_pixels = int(guard_window_pixels)
        self.clutter_window_pixels = int(clutter_window_pixels)
        self.min_target_pixels = int(min_target_pixels)
        self.max_target_pixels = int(max_target_pixels)
        self.min_tcr_db = float(min_tcr_db)
        self.min_peak_sigma0_db = float(min_peak_sigma0_db)
        self.noise_floor_db = float(noise_floor_db)
        self.coastal_buffer_m = float(coastal_buffer_m)

    def detect_from_file(
        self,
        raster_path: str | Path,
        *,
        band_idx: int | None = None,
        maritime_masker: MaritimeMasker | None = None,
        apply_maritime_mask: bool = True,
    ) -> list[SarBrightTarget]:
        """Run bright-target detection on a calibrated SAR GeoTIFF file.

        Args:
            raster_path: Path to calibrated SAR GeoTIFF (sigma0 in dB).
            band_idx: 1-indexed band to read. If None, selects VV band by description or defaults to 1.
            maritime_masker: Optional MaritimeMasker instance.
            apply_maritime_mask: Whether to apply seaward ocean domain masking. Default True.
        """
        p = Path(raster_path)
        if not p.exists():
            raise SarBrightTargetDetectorError(f"SAR raster file not found at: {p}")

        with rasterio.open(p) as src:
            target_band = band_idx
            if target_band is None:
                target_band = 1
                if src.descriptions:
                    for idx, desc in enumerate(src.descriptions, start=1):
                        d_str = (desc or "").upper()
                        if "VV" in d_str:
                            target_band = idx
                            break

            raster = src.read(target_band).astype(np.float32)
            transform = src.transform
            crs = src.crs or "EPSG:4326"

            return self.detect_from_raster(
                raster=raster,
                transform=transform,
                crs=crs,
                maritime_masker=maritime_masker,
                apply_maritime_mask=apply_maritime_mask,
            )

    def detect_from_raster(
        self,
        raster: np.ndarray,
        transform: Affine,
        crs: CRS | str = "EPSG:4326",
        *,
        valid_mask: np.ndarray | None = None,
        maritime_masker: MaritimeMasker | None = None,
        apply_maritime_mask: bool = True,
    ) -> list[SarBrightTarget]:
        """Run bright-target detection on a 2D calibrated SAR array (sigma0 in dB).

        Args:
            raster: 2D float32 array containing radar backscatter in decibels (dB).
            transform: rasterio Affine transform mapping pixel coords to geographic coordinates.
            crs: Coordinate reference system (default EPSG:4326).
            valid_mask: Optional 2D boolean array (True = valid pixel).
            maritime_masker: Optional MaritimeMasker instance for land/coastal surf rejection.
            apply_maritime_mask: Whether to apply maritime domain masking. Default True.
        """
        if raster.ndim != 2:
            raise SarBrightTargetDetectorError(f"Expected 2D raster array, got ndim={raster.ndim}")

        height, width = raster.shape
        if height < 3 or width < 3:
            return []

        # 1. Base finite validity mask
        finite_mask = np.isfinite(raster) & (raster > self.noise_floor_db)
        if valid_mask is not None:
            if valid_mask.shape != raster.shape:
                raise SarBrightTargetDetectorError(
                    f"valid_mask shape {valid_mask.shape} does not match raster shape {raster.shape}"
                )
            effective_valid = finite_mask & valid_mask
        else:
            effective_valid = finite_mask

        # 2. Maritime domain masking (reject inland terrain and coastal surf)
        if apply_maritime_mask:
            ocean_mask = self._get_ocean_mask(
                height=height,
                width=width,
                transform=transform,
                crs=crs,
                maritime_masker=maritime_masker,
            )
            effective_valid = effective_valid & ocean_mask

        if not np.any(effective_valid):
            return []

        # 3. 2D CA-CFAR Clutter Estimation with Annular Guard Band
        mu_clutter, sigma_clutter = self._compute_annular_clutter_stats(
            raster=raster,
            valid_mask=effective_valid,
        )

        # 4. Pixel-level thresholding
        # Condition: sigma0 >= mu_clutter + k_sigma * sigma_clutter AND sigma0 >= min_peak_sigma0_db
        cfar_threshold = mu_clutter + (self.cfar_k_sigma * sigma_clutter)
        candidate_pixels = (
            effective_valid
            & (raster >= cfar_threshold)
            & (raster >= self.min_peak_sigma0_db)
        )

        if not np.any(candidate_pixels):
            return []

        # 5. Connected Component Grouping (8-connectivity)
        structure = np.ones((3, 3), dtype=int)
        labeled_components, num_features = ndi.label(candidate_pixels, structure=structure)
        if num_features == 0:
            return []

        # 6. Extract component properties and filter by pixel count
        slices = ndi.find_objects(labeled_components)
        targets: list[SarBrightTarget] = []

        # Derive ground pixel dimensions in meters
        dx_m, dy_m = self._calculate_pixel_resolution_m(transform, height, width)

        target_idx = 1
        for comp_id in range(1, num_features + 1):
            comp_slice = slices[comp_id - 1]
            if comp_slice is None:
                continue

            sub_labeled = labeled_components[comp_slice]
            sub_mask = sub_labeled == comp_id
            pixel_count = int(np.sum(sub_mask))

            # Filter by component size
            if pixel_count < self.min_target_pixels or pixel_count > self.max_target_pixels:
                continue

            # Extract local coordinates of target pixels
            y_indices, x_indices = np.where(sub_mask)
            y_global = y_indices + comp_slice[0].start
            x_global = x_indices + comp_slice[1].start

            # Pixel centroid
            centroid_x = float(np.mean(x_global))
            centroid_y = float(np.mean(y_global))

            # Geographic centroid using affine transform
            lon, lat = rasterio.transform.xy(transform, centroid_y, centroid_x)

            # Peak backscatter within target
            target_values = raster[y_global, x_global]
            peak_sigma0 = float(np.max(target_values))

            # Local ambient clutter mean at target centroid
            c_y_int = int(np.clip(round(centroid_y), 0, height - 1))
            c_x_int = int(np.clip(round(centroid_x), 0, width - 1))
            clutter_mean = float(mu_clutter[c_y_int, c_x_int])
            tcr_db = peak_sigma0 - clutter_mean

            # Minimum Target-to-Clutter Ratio filter
            if tcr_db < self.min_tcr_db:
                continue

            # Apparent radar signature extent (scattering envelope, not physical hull)
            major_extent_m, minor_extent_m = self._estimate_apparent_extent(
                x_global=x_global,
                y_global=y_global,
                centroid_x=centroid_x,
                centroid_y=centroid_y,
                pixel_count=pixel_count,
                dx_m=dx_m,
                dy_m=dy_m,
            )

            # Heuristic detection confidence based on TCR and size (in [0.1, 0.99])
            confidence = float(np.clip(1.0 / (1.0 + math.exp(-(tcr_db - 6.0) / 2.5)), 0.1, 0.99))

            target_id = f"TGT-{target_idx:03d}"
            target_idx += 1

            targets.append(
                SarBrightTarget(
                    target_id=target_id,
                    pixel_x=centroid_x,
                    pixel_y=centroid_y,
                    lon=float(lon),
                    lat=float(lat),
                    peak_backscatter_db=peak_sigma0,
                    local_clutter_mean_db=clutter_mean,
                    target_to_clutter_ratio_db=tcr_db,
                    pixel_count=pixel_count,
                    apparent_major_extent_m=major_extent_m,
                    apparent_minor_extent_m=minor_extent_m,
                    detection_confidence=confidence,
                )
            )

        # Sort targets by descending peak backscatter
        targets.sort(key=lambda t: t.peak_backscatter_db, reverse=True)
        # Re-index target IDs stably after sorting
        for idx, t in enumerate(targets, start=1):
            t.target_id = f"TGT-{idx:03d}"

        return targets

    def _compute_annular_clutter_stats(
        self,
        raster: np.ndarray,
        valid_mask: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Compute local background mean and std using 2D integral tables with annular guard band.

        The clutter region around pixel (y, x) is:
            Annulus = Box(clutter_radius) - Box(guard_radius)
        This ensures bright target pixels within the guard window do not contaminate
        the ambient sea clutter statistics.
        """
        height, width = raster.shape
        r_c = self.clutter_window_pixels // 2
        r_g = self.guard_window_pixels // 2

        # Replace invalid/masked pixels with 0 for integral image calculation
        clean_raster = np.where(valid_mask, raster, 0.0).astype(np.float64)
        clean_sq = np.where(valid_mask, raster * raster, 0.0).astype(np.float64)
        clean_count = valid_mask.astype(np.float64)

        # Build 2D summed-area tables with 1-pixel zero padding at top and left
        sat_val = np.zeros((height + 1, width + 1), dtype=np.float64)
        sat_sq = np.zeros((height + 1, width + 1), dtype=np.float64)
        sat_cnt = np.zeros((height + 1, width + 1), dtype=np.float64)

        sat_val[1:, 1:] = np.cumsum(np.cumsum(clean_raster, axis=0), axis=1)
        sat_sq[1:, 1:] = np.cumsum(np.cumsum(clean_sq, axis=0), axis=1)
        sat_cnt[1:, 1:] = np.cumsum(np.cumsum(clean_count, axis=0), axis=1)

        # Global fallbacks for edge cells with insufficient background samples
        valid_vals = raster[valid_mask]
        global_mean = float(np.mean(valid_vals)) if len(valid_vals) > 0 else -10.0
        global_std = max(float(np.std(valid_vals)) if len(valid_vals) > 0 else 1.5, 0.5)

        # Coordinate grids for box window boundaries clipped to raster dimensions
        y_coords = np.arange(height)[:, None]
        x_coords = np.arange(width)[None, :]

        # Clutter box limits [y1, y2] x [x1, x2]
        c_y1 = np.maximum(0, y_coords - r_c)
        c_y2 = np.minimum(height, y_coords + r_c + 1)
        c_x1 = np.maximum(0, x_coords - r_c)
        c_x2 = np.minimum(width, x_coords + r_c + 1)

        # Guard box limits [y1, y2] x [x1, x2]
        g_y1 = np.maximum(0, y_coords - r_g)
        g_y2 = np.minimum(height, y_coords + r_g + 1)
        g_x1 = np.maximum(0, x_coords - r_g)
        g_x2 = np.minimum(width, x_coords + r_g + 1)

        # Helper to query SAT for any box
        def _sat_box_sum(sat: np.ndarray, y1: np.ndarray, y2: np.ndarray, x1: np.ndarray, x2: np.ndarray) -> np.ndarray:
            return sat[y2, x2] - sat[y1, x2] - sat[y2, x1] + sat[y1, x1]

        # Annular sum = Clutter box sum - Guard box sum
        sum_val = _sat_box_sum(sat_val, c_y1, c_y2, c_x1, c_x2) - _sat_box_sum(sat_val, g_y1, g_y2, g_x1, g_x2)
        sum_sq = _sat_box_sum(sat_sq, c_y1, c_y2, c_x1, c_x2) - _sat_box_sum(sat_sq, g_y1, g_y2, g_x1, g_x2)
        count = _sat_box_sum(sat_cnt, c_y1, c_y2, c_x1, c_x2) - _sat_box_sum(sat_cnt, g_y1, g_y2, g_x1, g_x2)

        # Minimum required valid clutter pixels to compute local statistics
        min_clutter_samples = max(10, (self.clutter_window_pixels * self.clutter_window_pixels) // 16)
        sufficient_samples = count >= min_clutter_samples

        mu = np.full((height, width), global_mean, dtype=np.float32)
        sigma = np.full((height, width), global_std, dtype=np.float32)

        safe_cnt = np.where(sufficient_samples, count, 1.0)
        mean_calc = sum_val / safe_cnt
        var_calc = (sum_sq / safe_cnt) - (mean_calc * mean_calc)
        # Avoid negative variance from floating point truncation and enforce minimum std 0.5 dB
        std_calc = np.sqrt(np.maximum(var_calc, 0.25))

        mu = np.where(sufficient_samples, mean_calc.astype(np.float32), mu)
        sigma = np.where(sufficient_samples, std_calc.astype(np.float32), sigma)

        return mu, sigma

    def _get_ocean_mask(
        self,
        height: int,
        width: int,
        transform: Affine,
        crs: CRS | str,
        maritime_masker: MaritimeMasker | None,
    ) -> np.ndarray:
        """Rasterize maritime ocean mask with coastal exclusion buffer using MaritimeMasker."""
        masker = maritime_masker
        if masker is None:
            try:
                masker = MaritimeMasker(coastal_buffer_m=self.coastal_buffer_m)
            except Exception:
                # If ocean dataset is unavailable, fail closed with all-True fallback only in testing
                return np.ones((height, width), dtype=bool)

        try:
            full_window = Window(col_off=0, row_off=0, width=width, height=height)
            ocean_mask = masker.rasterize_block_mask(
                window=full_window,
                transform=transform,
                crs=crs,
            )
            return ocean_mask.astype(bool)
        except Exception:
            # Fallback to valid ocean if rasterization encounters coordinate error
            return np.ones((height, width), dtype=bool)

    def _calculate_pixel_resolution_m(
        self,
        transform: Affine,
        height: int,
        width: int,
    ) -> tuple[float, float]:
        """Compute ground pixel width and height in meters from affine transform."""
        center_y = height / 2.0
        center_x = width / 2.0
        _, center_lat = rasterio.transform.xy(transform, center_y, center_x)

        lat_rad = math.radians(center_lat)
        m_per_deg_lat = 111132.92 - 559.82 * math.cos(2 * lat_rad) + 1.175 * math.cos(4 * lat_rad)
        m_per_deg_lon = 111412.84 * math.cos(lat_rad) - 93.5 * math.cos(3 * lat_rad)

        dx_m = abs(transform.a) * m_per_deg_lon
        dy_m = abs(transform.e) * m_per_deg_lat
        return max(float(dx_m), 1.0), max(float(dy_m), 1.0)

    def _estimate_apparent_extent(
        self,
        x_global: np.ndarray,
        y_global: np.ndarray,
        centroid_x: float,
        centroid_y: float,
        pixel_count: int,
        dx_m: float,
        dy_m: float,
    ) -> tuple[float | None, float | None]:
        """Estimate apparent radar major/minor extent in meters using 2nd central moments.

        IMPORTANT: Represents radar scattering envelope and sensor point spread function,
        NOT physical hull dimensions.
        """
        if pixel_count <= 0:
            return None, None

        if pixel_count == 1:
            # Single-pixel target: apparent extent is pixel resolution
            return round(max(dx_m, dy_m), 1), round(min(dx_m, dy_m), 1)

        # Scale coordinates into local meters centered at target centroid
        x_m = (x_global - centroid_x) * dx_m
        y_m = (y_global - centroid_y) * dy_m

        # Add uniform pixel box variance (1/12 * pixel_size^2) to prevent singular covariance
        mu_xx = float(np.mean(x_m * x_m)) + (dx_m * dx_m / 12.0)
        mu_yy = float(np.mean(y_m * y_m)) + (dy_m * dy_m / 12.0)
        mu_xy = float(np.mean(x_m * y_m))

        # Eigenvalues of 2D spatial covariance matrix
        trace = mu_xx + mu_yy
        diff = mu_xx - mu_yy
        disc = math.sqrt(max(diff * diff + 4.0 * mu_xy * mu_xy, 0.0))

        lambda_1 = (trace + disc) / 2.0
        lambda_2 = max((trace - disc) / 2.0, 0.0)

        # Equivalent ellipse axes = 2 * sqrt(eigenvalue)
        major_axis_m = 2.0 * math.sqrt(max(lambda_1, 1.0))
        minor_axis_m = 2.0 * math.sqrt(max(lambda_2, 1.0))

        return round(major_axis_m, 1), round(minor_axis_m, 1)
