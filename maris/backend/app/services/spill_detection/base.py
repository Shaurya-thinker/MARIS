"""Base interfaces, data structures, and errors for MARIS Stage B3 Spill Detection.

Modular architecture decoupling SAR raster acquisition from detection algorithm
and geometry extraction, enabling future ML/neural detector drop-in replacement.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np


class SpillDetectionError(Exception):
    """Raised when SAR oil spill detection or geometry extraction fails."""


@dataclass(frozen=True)
class DetectorResult:
    """Raw output of a SAR spill detection algorithm on a 2D calibrated raster."""

    mask: np.ndarray  # 2D boolean array (True = spill candidate, False = clean sea/invalid)
    probability: np.ndarray  # 2D float32 array in range [0.0, 1.0]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SpillRegionStats:
    """Quantitative properties of an individual connected spill candidate region."""

    region_id: int
    pixel_count: int
    area_m2: float
    area_km2: float
    min_db: float
    mean_db: float
    background_mean_db: float
    damping_contrast_db: float
    confidence: float
    bbox: tuple[float, float, float, float]  # west, south, east, north (WGS84)
    centroid: tuple[float, float]  # lon, lat (WGS84)
    geometry: dict[str, Any]  # GeoJSON Polygon


@dataclass(frozen=True)
class SpillDetectionResult:
    """Aggregated result of spill candidate detection across the entire SAR scene."""

    detected: bool
    confidence: float  # [0.0, 1.0]
    total_area_m2: float
    spill_count: int
    geometry: dict[str, Any]  # GeoJSON Polygon / MultiPolygon / GeometryCollection
    bbox: tuple[float, float, float, float] | None  # west, south, east, north
    centroid: tuple[float, float] | None  # lon, lat
    regions: list[SpillRegionStats]
    raster_stats: dict[str, Any]
    detector_metadata: dict[str, Any]


class BaseSpillDetector(ABC):
    """Abstract interface for SAR oil spill detection algorithms.

    Subclasses can implement deterministic baselines, CFAR detectors, or
    deep-learning segmentation networks (U-Net, DeepLab, etc.) without altering
    upstream preprocessing or downstream geometry extraction.
    """

    @property
    @abstractmethod
    def model_version(self) -> str:
        """Unique identifier and version for the detector algorithm."""

    @abstractmethod
    def detect(
        self,
        raster: np.ndarray,
        valid_mask: np.ndarray,
        polarization: str,
        pixel_size_m: tuple[float, float],
    ) -> DetectorResult:
        """Detect potential spill candidates in a calibrated sigma0 dB SAR raster.

        Args:
            raster: 2D float32 array of calibrated radar backscatter (sigma0 in dB).
            valid_mask: 2D boolean array where True indicates valid finite ocean pixels
                        and False indicates nodata, padding, land, or non-finite values.
            polarization: SAR polarization channel identifier ("VV", "VH", "HH", "HV").
            pixel_size_m: Ground pixel resolution (dx, dy) in meters.

        Returns:
            DetectorResult containing binary candidate mask, continuous probability map,
            and detector diagnostics.
        """
