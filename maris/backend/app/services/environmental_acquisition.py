"""Stage C1 — Automatic Environmental Acquisition Service for MARIS.

Connects an investigation or Sentinel-1 scene context to existing metocean data providers
(ERA5 10 m wind and CMEMS near-surface currents).

Pipeline:
1. Spatial & temporal framing (deriving buffered AOI and lookback/forward time window).
2. Provider acquisition execution (ERA5 / CMEMS).
3. A4 scientific data validation (Era5Validator, CmemsValidator).
4. AssetRegistry registration for validated artifacts only.
5. Domain model assembly (WindField, CurrentField).
6. Deterministic summary with explicit partial failure reporting.

Does NOT perform drift modelling, interpolation, backtracking, AIS processing, or forecasting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import math
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.acquisition.base import AcquisitionError, AcquisitionProvider
from app.acquisition.providers.cmems import CmemsAcquisitionProvider
from app.acquisition.providers.era5 import Era5AcquisitionProvider
from app.acquisition.registry import AssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact, AcquisitionRequest
from app.models.asset import Asset
from app.models.common import (
    AreaOfInterest,
    AssetType,
    BBoxAreaOfInterest,
    BoundingBox,
    EnvironmentKind,
    PolygonAreaOfInterest,
    TimeWindow,
)
from app.models.environment import CurrentField, Environment, WindField
from app.models.satellite import SatelliteScene
from app.validation.cmems import CmemsValidator
from app.validation.era5 import Era5Validator
from app.validation.schemas import ValidationResult, ValidationSeverity


class EnvironmentalAcquisitionError(Exception):
    """Raised when environmental acquisition framing or orchestration fails."""


class ProviderAcquisitionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED_ACQUISITION = "failed_acquisition"
    FAILED_VALIDATION = "failed_validation"
    SKIPPED = "skipped"


@dataclass
class EnvironmentalItemResult:
    provider_id: str
    status: ProviderAcquisitionStatus
    asset: Asset | None = None
    environment: Environment | None = None
    validation: ValidationResult | None = None
    error: str | None = None


@dataclass
class EnvironmentalAcquisitionSummary:
    investigation_id: str
    scene_id: str | None
    area_of_interest: AreaOfInterest
    time_window: TimeWindow
    items: dict[str, EnvironmentalItemResult] = field(default_factory=dict)
    succeeded_providers: list[str] = field(default_factory=list)
    failed_providers: list[str] = field(default_factory=list)


def utc(value: datetime) -> datetime:
    """Ensure datetime is timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def derive_environmental_bbox(
    area: AreaOfInterest,
    buffer_degrees: float = 0.25,
) -> BBoxAreaOfInterest:
    """Derive a geographic bounding box expanded by buffer_degrees.

    Default buffer_degrees (0.25 deg ~ 25 km) ensures sufficient spatial margin
    around the source footprint so ocean currents and wind fields encompass
    potential drift vectors without clipping. This is an acquisition framing default
    that Stage D modelling may override.
    """
    if buffer_degrees < 0.0:
        raise EnvironmentalAcquisitionError("buffer_degrees must be non-negative")

    if isinstance(area, BBoxAreaOfInterest):
        west = area.bbox.west
        south = area.bbox.south
        east = area.bbox.east
        north = area.bbox.north
    elif isinstance(area, PolygonAreaOfInterest):
        if not area.coordinates or not area.coordinates[0]:
            raise EnvironmentalAcquisitionError("PolygonAreaOfInterest has empty coordinates")
        lons = [pt[0] for pt in area.coordinates[0]]
        lats = [pt[1] for pt in area.coordinates[0]]
        west, east = min(lons), max(lons)
        south, north = min(lats), max(lats)
    else:
        raise EnvironmentalAcquisitionError(f"Unsupported AreaOfInterest type: {type(area)}")

    if west >= east:
        raise EnvironmentalAcquisitionError(
            f"Invalid longitude span: west ({west}) >= east ({east}). Dateline-crossing boxes are not supported."
        )
    if south >= north:
        raise EnvironmentalAcquisitionError(f"Invalid latitude span: south ({south}) >= north ({north})")

    buffered_west = max(-180.0, west - buffer_degrees)
    buffered_east = min(180.0, east + buffer_degrees)
    buffered_south = max(-90.0, south - buffer_degrees)
    buffered_north = min(90.0, north + buffer_degrees)

    if buffered_west >= buffered_east or buffered_south >= buffered_north:
        raise EnvironmentalAcquisitionError("Buffered bounding box collapsed or crossed boundaries")

    return BBoxAreaOfInterest(
        kind="bbox",
        bbox=BoundingBox(
            west=buffered_west,
            south=buffered_south,
            east=buffered_east,
            north=buffered_north,
        ),
    )


def derive_environmental_time_window(
    anchor_time: datetime,
    lookback_hours: float = 24.0,
    forward_hours: float = 6.0,
) -> TimeWindow:
    """Derive a TimeWindow spanning [anchor_time - lookback_hours, anchor_time + forward_hours].

    Default framing:
    - lookback_hours: 24.0 h (covers preceding atmospheric/current forcing for backtrack analysis)
    - forward_hours: 6.0 h (covers near-term continuation around sensing instant)
    These are acquisition framing parameters, not validated drift physics constants.
    """
    if lookback_hours < 0.0 or forward_hours < 0.0:
        raise EnvironmentalAcquisitionError("lookback_hours and forward_hours must be non-negative")

    anchor_utc = utc(anchor_time)
    start = anchor_utc - timedelta(hours=lookback_hours)
    end = anchor_utc + timedelta(hours=forward_hours)

    if start >= end:
        raise EnvironmentalAcquisitionError(f"Derived time window start ({start}) >= end ({end})")

    return TimeWindow(start=start, end=end)


def acquire_environmental_data_for_investigation(
    investigation_id: str,
    area_of_interest: AreaOfInterest,
    time_window: TimeWindow,
    scene_id: str | None = None,
    providers: list[str] | None = None,
    spatial_buffer_degrees: float = 0.25,
    lookback_hours: float = 24.0,
    forward_hours: float = 6.0,
    apply_framing: bool = True,
    era5_provider: Era5AcquisitionProvider | None = None,
    cmems_provider: CmemsAcquisitionProvider | None = None,
    registry: AssetRegistry | None = None,
) -> EnvironmentalAcquisitionSummary:
    """Orchestrate automatic environmental data acquisition for an investigation.

    Flow:
    1. Frames AOI with spatial buffer and TimeWindow with lookback/forward duration (if apply_framing=True).
    2. Sequentially requests ERA5 wind and CMEMS currents through existing providers.
    3. Runs A4.3 Era5Validator and A4.4 CmemsValidator on acquired artifacts.
    4. Registers ONLY validated artifacts in AssetRegistry.
    5. Assembles WindField and CurrentField domain objects.
    6. Retains complete provenance linking to parent investigation and scene_id.
    7. Tracks success/failure of each provider explicitly (partial failures do not fail the whole run).
    """
    target_providers = [p.strip().lower() for p in (providers or ["era5", "cmems"])]
    target_registry = registry or default_asset_registry

    # 1. Framing
    if apply_framing:
        framed_aoi = derive_environmental_bbox(area_of_interest, buffer_degrees=spatial_buffer_degrees)
        # Use midpoint of time window as anchor if adjusting
        midpoint = time_window.start + (time_window.end - time_window.start) / 2
        framed_window = derive_environmental_time_window(
            anchor_time=midpoint,
            lookback_hours=lookback_hours,
            forward_hours=forward_hours,
        )
    else:
        if isinstance(area_of_interest, BBoxAreaOfInterest):
            framed_aoi = area_of_interest
        else:
            framed_aoi = derive_environmental_bbox(area_of_interest, buffer_degrees=0.0)
        framed_window = time_window

    summary = EnvironmentalAcquisitionSummary(
        investigation_id=investigation_id,
        scene_id=scene_id,
        area_of_interest=framed_aoi,
        time_window=framed_window,
    )

    # 2. Acquire & Validate ERA5 Wind
    if "era5" in target_providers:
        provider = era5_provider or Era5AcquisitionProvider()
        req = AcquisitionRequest(
            investigation_id=investigation_id,
            provider_id="era5",
            asset_type=AssetType.ENVIRONMENT_WIND,
            area_of_interest=framed_aoi,
            time_window=framed_window,
            metadata={"parent_scene_id": scene_id} if scene_id else {},
        )
        try:
            acq_result = provider.acquire(req)
            if not acq_result.artifacts:
                raise AcquisitionError("ERA5 provider returned no artifacts")

            artifact = acq_result.artifacts[0]
            artifact_path = Path(artifact.location)
            validator = Era5Validator()

            if artifact_path.is_dir():
                nc_files = sorted(artifact_path.glob("*.nc"))
                if not nc_files:
                    val_result = validator.validate(artifact)
                else:
                    all_passed = True
                    combined_issues = []
                    last_res = None
                    for nc_file in nc_files:
                        sub_res = validator.validate(artifact.model_copy(update={"location": str(nc_file)}))
                        last_res = sub_res
                        if not sub_res.passed:
                            all_passed = False
                            combined_issues.extend(sub_res.issues)
                    val_result = ValidationResult(
                        artifact_location=artifact.location,
                        validator_name=validator.name,
                        validator_version=validator.version,
                        validated_at=datetime.now(timezone.utc),
                        issues=combined_issues,
                        metadata={
                            "validation_classification": "PREFERRED" if all_passed else "INVALID",
                            "file_count": len(nc_files),
                        },
                    )
            else:
                val_result = validator.validate(artifact)

            if not val_result.passed:
                err_issues = [i.message for i in val_result.issues if i.severity == ValidationSeverity.ERROR]
                err_msg = "; ".join(err_issues) or "ERA5 validation failed"
                summary.items["era5"] = EnvironmentalItemResult(
                    provider_id="era5",
                    status=ProviderAcquisitionStatus.FAILED_VALIDATION,
                    validation=val_result,
                    error=err_msg,
                )
                summary.failed_providers.append("era5")
            else:
                # Register accepted asset in registry
                asset = target_registry.register(investigation_id, "era5", artifact)

                wind_field = WindField(
                    id=f"wind-{investigation_id}-{asset.id}",
                    investigation_id=investigation_id,
                    asset_id=asset.id,
                    kind=EnvironmentKind.WIND,
                    provider="era5",
                    time_window=framed_window,
                    spatial_bounds=framed_aoi.bbox,
                    variables={"variables": ["10m_u_component_of_wind", "10m_v_component_of_wind"]},
                    metadata={
                        "parent_scene_id": scene_id,
                        "validation_classification": val_result.metadata.get("validation_classification"),
                        "artifact_location": artifact.location,
                    },
                )
                summary.items["era5"] = EnvironmentalItemResult(
                    provider_id="era5",
                    status=ProviderAcquisitionStatus.SUCCEEDED,
                    asset=asset,
                    environment=wind_field,
                    validation=val_result,
                )
                summary.succeeded_providers.append("era5")

        except Exception as exc:
            summary.items["era5"] = EnvironmentalItemResult(
                provider_id="era5",
                status=ProviderAcquisitionStatus.FAILED_ACQUISITION,
                error=str(exc),
            )
            summary.failed_providers.append("era5")

    # 3. Acquire & Validate CMEMS Currents
    if "cmems" in target_providers:
        provider = cmems_provider or CmemsAcquisitionProvider()
        req = AcquisitionRequest(
            investigation_id=investigation_id,
            provider_id="cmems",
            asset_type=AssetType.ENVIRONMENT_CURRENT,
            area_of_interest=framed_aoi,
            time_window=framed_window,
            metadata={"parent_scene_id": scene_id} if scene_id else {},
        )
        try:
            acq_result = provider.acquire(req)
            if not acq_result.artifacts:
                raise AcquisitionError("CMEMS provider returned no artifacts")

            artifact = acq_result.artifacts[0]
            # Validate artifact using A4.4 validator
            validator = CmemsValidator()
            val_result = validator.validate(artifact)

            if not val_result.passed:
                err_issues = [i.message for i in val_result.issues if i.severity == ValidationSeverity.ERROR]
                err_msg = "; ".join(err_issues) or "CMEMS validation failed"
                summary.items["cmems"] = EnvironmentalItemResult(
                    provider_id="cmems",
                    status=ProviderAcquisitionStatus.FAILED_VALIDATION,
                    validation=val_result,
                    error=err_msg,
                )
                summary.failed_providers.append("cmems")
            else:
                # Register accepted asset in registry
                asset = target_registry.register(investigation_id, "cmems", artifact)

                current_field = CurrentField(
                    id=f"current-{investigation_id}-{asset.id}",
                    investigation_id=investigation_id,
                    asset_id=asset.id,
                    kind=EnvironmentKind.CURRENT,
                    provider="cmems",
                    time_window=framed_window,
                    spatial_bounds=framed_aoi.bbox,
                    variables={"variables": ["uo", "vo"]},
                    metadata={
                        "parent_scene_id": scene_id,
                        "validation_classification": val_result.metadata.get("validation_classification"),
                        "artifact_location": artifact.location,
                        "near_surface_depth": val_result.metadata.get("near_surface_depth"),
                    },
                )
                summary.items["cmems"] = EnvironmentalItemResult(
                    provider_id="cmems",
                    status=ProviderAcquisitionStatus.SUCCEEDED,
                    asset=asset,
                    environment=current_field,
                    validation=val_result,
                )
                summary.succeeded_providers.append("cmems")

        except Exception as exc:
            summary.items["cmems"] = EnvironmentalItemResult(
                provider_id="cmems",
                status=ProviderAcquisitionStatus.FAILED_ACQUISITION,
                error=str(exc),
            )
            summary.failed_providers.append("cmems")

    return summary


def acquire_environmental_data_for_scene(
    scene: SatelliteScene,
    investigation_id: str | None = None,
    providers: list[str] | None = None,
    spatial_buffer_degrees: float = 0.25,
    lookback_hours: float = 24.0,
    forward_hours: float = 6.0,
    era5_provider: Era5AcquisitionProvider | None = None,
    cmems_provider: CmemsAcquisitionProvider | None = None,
    registry: AssetRegistry | None = None,
) -> EnvironmentalAcquisitionSummary:
    """Convenience entry point to derive and acquire environmental data directly for a SatelliteScene."""
    inv_id = investigation_id or scene.investigation_id

    # Derive bounding box and time window anchored at scene sensing instant
    framed_aoi = derive_environmental_bbox(scene.footprint, buffer_degrees=spatial_buffer_degrees)
    framed_window = derive_environmental_time_window(
        anchor_time=scene.acquisition_time,
        lookback_hours=lookback_hours,
        forward_hours=forward_hours,
    )

    return acquire_environmental_data_for_investigation(
        investigation_id=inv_id,
        area_of_interest=framed_aoi,
        time_window=framed_window,
        scene_id=scene.id,
        providers=providers,
        apply_framing=False,  # Already framed directly around scene
        era5_provider=era5_provider,
        cmems_provider=cmems_provider,
        registry=registry,
    )
