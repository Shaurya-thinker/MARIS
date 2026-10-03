"""MARIS Real-Experiment — SAR Surveillance Orchestration (Phase 6).

Coordinates CA-CFAR bright radar target detection on calibrated Sentinel-1 SAR imagery
and spatiotemporal correlation against authentic AIS transponder tracks.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any

from app.services.real_experiment.sar_ais_matching_service import (
    DEFAULT_SURVEILLANCE_DISCLAIMER,
    SarAisMatchingResult,
    SarAisMatchingService,
    parse_utc_timestamp,
)
from app.services.real_experiment.sar_subscene_fixture import (
    DEFAULT_CORSICA_SUBSCENE_PATH,
    generate_corsica_sar_subscene,
)
from app.services.real_experiment.sar_vessel_detector import (
    SarBrightTarget,
    SarBrightTargetDetector,
)

logger = logging.getLogger(__name__)


@dataclass
class SarSurveillanceConfig:
    """Configurable gates and CFAR settings for Phase 6 surveillance."""
    enabled: bool = False
    sar_raster_path: str | None = None
    cfar_k_sigma: float = 4.0
    guard_band_pixels: int = 15
    clutter_band_pixels: int = 41
    tcr_threshold_db: float = 4.5
    min_pixels: int = 2
    max_pixels: int = 2000
    coincident_spatial_gate_m: float = 1000.0
    max_association_gate_m: float = 3000.0
    temporal_window_minutes: float = 30.0
    max_interpolation_interval_s: float = 900.0
    coincident_time_threshold_s: float = 60.0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SarSurveillanceConfig:
        if not data:
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            sar_raster_path=data.get("sar_raster_path"),
            cfar_k_sigma=float(data.get("cfar_k_sigma", 4.0)),
            guard_band_pixels=int(data.get("guard_band_pixels", 15)),
            clutter_band_pixels=int(data.get("clutter_band_pixels", 41)),
            tcr_threshold_db=float(data.get("tcr_threshold_db", 4.5)),
            min_pixels=int(data.get("min_pixels", 2)),
            max_pixels=int(data.get("max_pixels", 2000)),
            coincident_spatial_gate_m=float(data.get("coincident_spatial_gate_m", 1000.0)),
            max_association_gate_m=float(data.get("max_association_gate_m", 3000.0)),
            temporal_window_minutes=float(data.get("temporal_window_minutes", 30.0)),
            max_interpolation_interval_s=float(data.get("max_interpolation_interval_s", 900.0)),
            coincident_time_threshold_s=float(data.get("coincident_time_threshold_s", 60.0)),
        )


def execute_sar_surveillance(
    sar_raster_path: str | None = None,
    observation_lon: float = 0.0,
    observation_lat: float = 0.0,
    observation_time: datetime | str | None = None,
    search_bbox: list[float] | None = None,
    ais_vessels: list[dict[str, Any]] | None = None,
    config: SarSurveillanceConfig | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute CFAR SAR bright-target detection and AIS spatiotemporal correlation."""
    if isinstance(config, dict):
        cfg = SarSurveillanceConfig.from_dict(config)
    elif isinstance(config, SarSurveillanceConfig):
        cfg = config
    else:
        cfg = SarSurveillanceConfig()

    # Time normalization
    if observation_time is None:
        t_obs = datetime(2018, 10, 8, 5, 30, tzinfo=timezone.utc)
    else:
        t_obs = parse_utc_timestamp(observation_time)

    # Resolve SAR raster path
    effective_raster = sar_raster_path or cfg.sar_raster_path
    if not effective_raster or not Path(effective_raster).exists():
        if DEFAULT_CORSICA_SUBSCENE_PATH.exists():
            effective_raster = str(DEFAULT_CORSICA_SUBSCENE_PATH)
        else:
            try:
                gen_path = generate_corsica_sar_subscene()
                effective_raster = str(gen_path)
            except Exception as exc:
                logger.warning("Could not generate SAR subscene fixture: %s", exc)
                effective_raster = None

    # Normalization of CFAR odd window dimensions
    guard_w = cfg.guard_band_pixels if cfg.guard_band_pixels % 2 == 1 else cfg.guard_band_pixels + 1
    if guard_w < 3:
        guard_w = 3
    clutter_w = cfg.clutter_band_pixels if cfg.clutter_band_pixels % 2 == 1 else cfg.clutter_band_pixels + 1
    if clutter_w <= guard_w:
        clutter_w = guard_w + 2

    # Scene bbox default if not provided: orchestration search-area buffer (±0.15°)
    # NOTE: This defines the spatial search domain for radar target detection and AIS vessel
    # correlation when no explicit bbox is supplied. It serves strictly as an orchestration
    # search-area default and does NOT modify or change scientific detection thresholds.
    if search_bbox and len(search_bbox) == 4:
        bbox = (float(search_bbox[0]), float(search_bbox[1]), float(search_bbox[2]), float(search_bbox[3]))
    elif observation_lon != 0.0 and observation_lat != 0.0:
        d = 0.15
        bbox = (observation_lon - d, observation_lat - d, observation_lon + d, observation_lat + d)
    else:
        bbox = (9.3800, 43.1500, 9.5800, 43.3500)

    # 1. Run CA-CFAR Bright-Target Detection
    targets: list[SarBrightTarget] = []
    if effective_raster and Path(effective_raster).exists():
        try:
            detector = SarBrightTargetDetector(
                cfar_k_sigma=cfg.cfar_k_sigma,
                guard_window_pixels=guard_w,
                clutter_window_pixels=clutter_w,
                min_target_pixels=cfg.min_pixels,
                max_target_pixels=cfg.max_pixels,
                min_tcr_db=cfg.tcr_threshold_db,
            )
            targets = detector.detect_from_file(
                raster_path=effective_raster,
                bbox=bbox,
                apply_maritime_mask=True,
            )
            logger.info("Detected %d SAR bright targets in %s", len(targets), effective_raster)
        except Exception as exc:
            logger.error("SAR vessel detection failed: %s", exc)

    # 2. Run Spatiotemporal Correlation
    matcher = SarAisMatchingService(
        coincident_spatial_gate_m=cfg.coincident_spatial_gate_m,
        max_association_gate_m=cfg.max_association_gate_m,
        temporal_window_minutes=cfg.temporal_window_minutes,
        max_interpolation_interval_s=cfg.max_interpolation_interval_s,
        coincident_time_threshold_s=cfg.coincident_time_threshold_s,
    )

    if ais_vessels is not None and len(ais_vessels) > 0:
        match_result = matcher.correlate(
            sar_targets=targets,
            ais_vessels=ais_vessels,
            observation_time=t_obs,
            scene_bbox=bbox,
        )
    else:
        match_result = matcher.correlate_from_db(
            sar_targets=targets,
            observation_time=t_obs,
            scene_bbox=bbox,
        )

    res_dict = match_result.as_dict()

    # Format targets for schema
    target_schemas = []
    for t in targets:
        target_schemas.append({
            "target_id": t.target_id,
            "pixel_x": int(round(t.pixel_x)),
            "pixel_y": int(round(t.pixel_y)),
            "lon": round(t.lon, 6),
            "lat": round(t.lat, 6),
            "peak_backscatter_db": round(t.peak_backscatter_db, 2),
            "local_clutter_mean_db": round(t.local_clutter_mean_db, 2),
            "target_to_clutter_ratio_db": round(t.target_to_clutter_ratio_db, 2),
            "pixel_count": t.pixel_count,
            "bounding_box_pixels": [
                int(round(t.pixel_x - 2)),
                int(round(t.pixel_y - 2)),
                int(round(t.pixel_x + 2)),
                int(round(t.pixel_y + 2)),
            ],
        })

    res_dict["targets"] = target_schemas
    return res_dict
