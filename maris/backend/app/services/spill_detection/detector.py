"""Adaptive local thresholding baseline detector for SAR marine oil spill candidates.

Scientific Basis & Principle:
----------------------------
In Sentinel-1 SAR imagery, mineral oil dampens capillary and short gravity ocean waves
(Bragg scattering mechanism). This reduces surface roughness and causes the radar pulse
to reflect specularly away from the sensor, producing distinct dark patches (radar backscatter
sigma0 significantly lower than the surrounding ambient sea clutter).

Polarization Handling:
----------------------
VV (Vertical-Vertical) polarization is the primary and scientifically optimal channel for
oil spill detection because it exhibits a substantially higher signal-to-clutter ratio (SCR)
over sea surfaces compared to cross-polarization (VH).
VH cross-polarization is dominated by volume scattering and instrument noise floor (NESZ)
at low backscatter, which reduces dark spot contrast.
VV and VH are processed independently and never arbitrarily averaged or combined.

Algorithm Architecture:
-----------------------
1. Validation and nodata/NaN masking (excluding non-finite pixels, sensor zero-padding,
   and values below physical radar noise floor).
2. Adaptive local background statistics estimation:
   A sliding 2D box window computes the local ambient sea clutter mean (mu_bg) and standard
   deviation (sigma_bg) using summed-area integral tables. This accounts for incidence angle
   variation across the swath and regional sea state / wind gradients.
3. Dual-criteria dark-spot thresholding:
   A pixel is marked as a candidate if:
   (a) Damping contrast: Delta sigma0 = mu_bg - sigma0 >= damping_threshold_db (default: 3.5 dB)
   (b) Statistical anomaly: sigma0 <= mu_bg - k_sigma * sigma_bg (default: k=2.0)
4. Continuous candidate probability mapping:
   A smooth logistic sigmoid mapping in [0.0, 1.0] representing normalized damping confidence.
5. Morphological cleanup & boundary exclusion:
   3x3 binary opening (erosion followed by dilation) removes isolated speckle spikes and
   narrows false bridges. Pixels within border margins of nodata/land/padding are excluded.

Scientific Caveats & Limitations:
---------------------------------
This detector is an automated BASELINE anomaly detector, NOT a validated oil-spill classifier.
Low-backscatter dark formations in SAR are also caused by natural "look-alikes":
- Low wind areas / calm ocean zones (< 2-3 m/s) where capillary waves cannot form.
- Natural biogenic slicks (algal blooms, fish oils, natural surfactants).
- Atmospheric phenomena (rain cells, atmospheric gravity waves, wind shadows of land).
- Oceanographic features (internal waves, upwelling, grease ice / fresh ice).
This baseline detector does NOT claim legal certainty or final attribution.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.services.spill_detection.base import BaseSpillDetector, DetectorResult, SpillDetectionError


class AdaptiveThresholdSpillDetector(BaseSpillDetector):
    """Deterministic adaptive SAR dark-spot baseline detector for marine oil spill candidates."""

    MODEL_VERSION = "maris.b3.sar_adaptive_baseline.v1"

    def __init__(
        self,
        damping_threshold_db: float = 3.5,
        k_sigma: float = 2.0,
        window_size_pixels: int = 51,
        morphological_opening: bool = True,
        border_margin_pixels: int = 2,
        noise_floor_db: float = -45.0,
    ) -> None:
        """Initialize the detector with explicit scientific parameters.

        Args:
            damping_threshold_db: Minimum required backscatter damping (dB) below local sea
                                  background mean (mu_bg - sigma0). Typical oil damping ranges
                                  from 3.0 to 10+ dB. Default: 3.5 dB.
            k_sigma: Number of background standard deviations below mean for statistical
                     anomaly threshold (sigma0 <= mu_bg - k * sigma_bg). Default: 2.0.
            window_size_pixels: Window size (in pixels) for local background estimation.
                                Must be an odd positive integer. At 10m spacing, 51px ~ 510m.
                                Default: 51.
            morphological_opening: Whether to apply 3x3 binary opening to suppress single-pixel
                                   speckle noise. Default: True.
            border_margin_pixels: Pixel margin to mask around valid data / nodata boundaries to
                                  prevent edge-filtering artifacts. Default: 2.
            noise_floor_db: Absolute backscatter floor (dB). Pixels at or below this value are
                            dominated by thermal noise floor and excluded. Default: -45.0 dB.
        """
        if damping_threshold_db <= 0:
            raise ValueError("damping_threshold_db must be positive")
        if k_sigma < 0:
            raise ValueError("k_sigma must be non-negative")
        if window_size_pixels < 3 or window_size_pixels % 2 == 0:
            raise ValueError("window_size_pixels must be an odd integer >= 3")
        if border_margin_pixels < 0:
            raise ValueError("border_margin_pixels must be non-negative")

        self.damping_threshold_db = float(damping_threshold_db)
        self.k_sigma = float(k_sigma)
        self.window_size_pixels = int(window_size_pixels)
        self.morphological_opening = bool(morphological_opening)
        self.border_margin_pixels = int(border_margin_pixels)
        self.noise_floor_db = float(noise_floor_db)

    @property
    def model_version(self) -> str:
        return self.MODEL_VERSION

    def detect(
        self,
        raster: np.ndarray,
        valid_mask: np.ndarray,
        polarization: str,
        pixel_size_m: tuple[float, float],
    ) -> DetectorResult:
        """Run adaptive dark-spot candidate detection on a calibrated SAR sigma0 dB raster.

        Args:
            raster: 2D float32 array of calibrated sigma0 backscatter in decibels (dB).
            valid_mask: 2D boolean array (True = valid finite ocean pixel).
            polarization: SAR polarization channel ("VV", "VH", "HH", "HV").
            pixel_size_m: (pixel_width_meters, pixel_height_meters).
        """
        if raster.ndim != 2:
            raise SpillDetectionError(f"Expected 2D raster array, got ndim={raster.ndim}")
        if valid_mask.shape != raster.shape:
            raise SpillDetectionError(
                f"valid_mask shape {valid_mask.shape} does not match raster shape {raster.shape}"
            )

        height, width = raster.shape

        # 1. Refine valid mask to exclude non-finite and below-noise-floor values
        finite_mask = np.isfinite(raster) & valid_mask & (raster > self.noise_floor_db)
        valid_pixel_count = int(np.sum(finite_mask))

        if valid_pixel_count == 0:
            # Entire scene is nodata/invalid
            empty_mask = np.zeros((height, width), dtype=bool)
            empty_prob = np.zeros((height, width), dtype=np.float32)
            return DetectorResult(
                mask=empty_mask,
                probability=empty_prob,
                metadata={
                    "model_version": self.model_version,
                    "polarization": polarization,
                    "valid_pixel_count": 0,
                    "candidate_pixel_count": 0,
                    "reason": "no_valid_finite_pixels",
                },
            )

        # 2. Border margin exclusion (erode valid mask so border pixels are not false positives)
        effective_valid = finite_mask.copy()
        if self.border_margin_pixels > 0 and valid_pixel_count > 10:
            effective_valid = self._erode_mask_n(effective_valid, self.border_margin_pixels)

        # 3. Robust ambient sea clutter estimation:
        # Exclude deep dark anomalies from contaminating background statistics (CFAR principle)
        valid_vals = raster[effective_valid]
        if len(valid_vals) > 0:
            ambient_sea_ref = float(np.percentile(valid_vals, 70))
            bg_sample_mask = effective_valid & (raster >= (ambient_sea_ref - 2.5))
            if np.sum(bg_sample_mask) < 10:
                bg_sample_mask = effective_valid
        else:
            bg_sample_mask = effective_valid

        # Compute local background statistics (mu_bg, sigma_bg) from clean sea samples
        mu_bg, sigma_bg = self._compute_local_background_stats(
            raster=raster,
            valid_mask=effective_valid,
            sample_mask=bg_sample_mask,
            window_size=self.window_size_pixels,
        )

        # 4. Adaptive thresholding
        # Condition 1: Damping contrast Delta sigma0 >= damping_threshold_db
        # Condition 2: Statistical threshold sigma0 <= mu_bg - k_sigma * sigma_bg OR strong damping
        damping_contrast = np.where(effective_valid, mu_bg - raster, 0.0)
        stat_threshold = mu_bg - (self.k_sigma * sigma_bg)
        stat_anomaly = (raster <= stat_threshold) | (damping_contrast >= 1.75 * self.damping_threshold_db)

        raw_candidates = (
            effective_valid
            & (damping_contrast >= self.damping_threshold_db)
            & stat_anomaly
        )

        # 5. Morphological cleanup (3x3 opening) to eliminate single-pixel speckle
        if self.morphological_opening and np.any(raw_candidates):
            clean_candidates = self._binary_opening_3x3(raw_candidates)
        else:
            clean_candidates = raw_candidates

        # Ensure no invalid pixels are flagged
        clean_candidates = clean_candidates & effective_valid

        # 6. Continuous probability map P(spill) in [0.0, 1.0]
        # Smooth logistic mapping centered at the damping threshold:
        # z = (damping_contrast - damping_threshold_db) / max(sigma_bg, 0.5)
        with np.errstate(over="ignore", under="ignore"):
            denom = np.maximum(sigma_bg, 0.5)
            z = (damping_contrast - self.damping_threshold_db) / denom
            prob = np.where(
                effective_valid,
                1.0 / (1.0 + np.exp(-1.5 * z)),
                0.0,
            ).astype(np.float32)

            # Suppress probability on non-candidate regions for cleaner segmentation
            prob = np.where(clean_candidates, prob, 0.0).astype(np.float32)

        # 7. Collect diagnostics & metadata
        candidate_count = int(np.sum(clean_candidates))
        bg_samples = raster[bg_sample_mask]
        bg_mean_global = float(np.mean(bg_samples)) if len(bg_samples) > 0 else float("nan")
        bg_std_global = float(np.std(bg_samples)) if len(bg_samples) > 0 else float("nan")

        metadata: dict[str, Any] = {
            "model_version": self.model_version,
            "polarization": polarization,
            "parameters": {
                "damping_threshold_db": self.damping_threshold_db,
                "k_sigma": self.k_sigma,
                "window_size_pixels": self.window_size_pixels,
                "morphological_opening": self.morphological_opening,
                "border_margin_pixels": self.border_margin_pixels,
                "noise_floor_db": self.noise_floor_db,
            },
            "valid_pixel_count": valid_pixel_count,
            "candidate_pixel_count": candidate_count,
            "candidate_pixel_ratio": float(candidate_count / valid_pixel_count) if valid_pixel_count > 0 else 0.0,
            "global_background_mean_db": bg_mean_global,
            "global_background_std_db": bg_std_global,
        }

        return DetectorResult(
            mask=clean_candidates,
            probability=prob,
            metadata=metadata,
        )

    def _compute_local_background_stats(
        self,
        raster: np.ndarray,
        valid_mask: np.ndarray,
        sample_mask: np.ndarray,
        window_size: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Compute local background mean and std over clean sea clutter using 2D integral images."""
        height, width = raster.shape
        rad = window_size // 2

        sample_count_total = np.sum(sample_mask)
        if height < window_size or width < window_size or sample_count_total < (window_size * 2):
            if sample_count_total > 0:
                g_mean = float(np.mean(raster[sample_mask]))
                g_std = max(float(np.std(raster[sample_mask])), 0.5)
            elif np.sum(valid_mask) > 0:
                g_mean = float(np.mean(raster[valid_mask]))
                g_std = max(float(np.std(raster[valid_mask])), 0.5)
            else:
                g_mean, g_std = 0.0, 1.0
            return np.full((height, width), g_mean, dtype=np.float32), np.full(
                (height, width), g_std, dtype=np.float32
            )

        # Zero out excluded/non-sample pixels for integral image accumulation
        val_arr = np.where(sample_mask, raster, 0.0).astype(np.float64)
        val_sq_arr = np.where(sample_mask, raster**2, 0.0).astype(np.float64)
        cnt_arr = sample_mask.astype(np.float64)

        # Pad with zeros on top and left for 1-indexed integral table lookups
        sat_val = np.pad(np.cumsum(np.cumsum(val_arr, axis=0), axis=1), ((1, 0), (1, 0)))
        sat_val_sq = np.pad(np.cumsum(np.cumsum(val_sq_arr, axis=0), axis=1), ((1, 0), (1, 0)))
        sat_cnt = np.pad(np.cumsum(np.cumsum(cnt_arr, axis=0), axis=1), ((1, 0), (1, 0)))

        # Coordinate grids for window boundaries
        y_idx = np.arange(height)[:, np.newaxis]
        x_idx = np.arange(width)[np.newaxis, :]

        y1 = np.maximum(0, y_idx - rad)
        y2 = np.minimum(height, y_idx + rad + 1)
        x1 = np.maximum(0, x_idx - rad)
        x2 = np.minimum(width, x_idx + rad + 1)

        # Window sums via 4-corner lookups in integral table
        sum_val = sat_val[y2, x2] - sat_val[y1, x2] - sat_val[y2, x1] + sat_val[y1, x1]
        sum_val_sq = sat_val_sq[y2, x2] - sat_val_sq[y1, x2] - sat_val_sq[y2, x1] + sat_val_sq[y1, x1]
        cnt = sat_cnt[y2, x2] - sat_cnt[y1, x2] - sat_cnt[y2, x1] + sat_cnt[y1, x1]

        # Minimum required valid pixels in window to compute reliable local stats
        min_win_pixels = max(9, (window_size * window_size) // 16)
        valid_win = cnt >= min_win_pixels

        safe_cnt = np.maximum(cnt, 1.0)
        mean_win = sum_val / safe_cnt
        var_win = (sum_val_sq / safe_cnt) - (mean_win**2)
        std_win = np.sqrt(np.maximum(var_win, 0.0))

        # Global fallback for window positions with too few sample pixels
        global_mean = float(np.mean(raster[sample_mask]))
        global_std = max(float(np.std(raster[sample_mask])), 0.5)

        mu_bg = np.where(valid_win, mean_win, global_mean).astype(np.float32)
        sigma_bg = np.where(valid_win, np.maximum(std_win, 0.5), global_std).astype(np.float32)

        return mu_bg, sigma_bg

    def _binary_erosion_3x3(self, arr: np.ndarray) -> np.ndarray:
        """3x3 binary erosion using 8-connectivity."""
        pad = np.pad(arr, 1, mode="constant", constant_values=False)
        return (
            pad[:-2, :-2]
            & pad[:-2, 1:-1]
            & pad[:-2, 2:]
            & pad[1:-1, :-2]
            & arr
            & pad[1:-1, 2:]
            & pad[2:, :-2]
            & pad[2:, 1:-1]
            & pad[2:, 2:]
        )

    def _binary_dilation_3x3(self, arr: np.ndarray) -> np.ndarray:
        """3x3 binary dilation using 8-connectivity."""
        pad = np.pad(arr, 1, mode="constant", constant_values=False)
        return (
            pad[:-2, :-2]
            | pad[:-2, 1:-1]
            | pad[:-2, 2:]
            | pad[1:-1, :-2]
            | arr
            | pad[1:-1, 2:]
            | pad[2:, :-2]
            | pad[2:, 1:-1]
            | pad[2:, 2:]
        )

    def _binary_opening_3x3(self, arr: np.ndarray) -> np.ndarray:
        """3x3 morphological opening (erosion followed by dilation)."""
        return self._binary_dilation_3x3(self._binary_erosion_3x3(arr))

    def _erode_mask_n(self, mask: np.ndarray, iterations: int) -> np.ndarray:
        """Erode boolean mask n times to enforce border margin."""
        res = mask.copy()
        for _ in range(iterations):
            res = self._binary_erosion_3x3(res)
        return res
