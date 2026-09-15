from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.acquisition.registry import default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.core.config import settings
from app.models.asset import Asset
from app.models.common import (
    AreaOfInterest,
    AssetType,
    BBoxAreaOfInterest,
    BoundingBox,
    InvestigationStatus,
    Provenance,
    TimeWindow,
)
from app.models.behavioral_intelligence import (
    BehavioralIntelligenceRequest,
    BehavioralIntelligenceResult,
)
from app.models.drift import DriftResult
from app.models.satellite import SatelliteScene, SpillDetection
from app.models.source_estimation import SourceEstimateResult
from app.models.trajectory_analysis import TrajectoryAnalysisResult
from app.models.vessel import CandidateVesselGenerationResult
from app.services.behavioral_intelligence import (
    BehavioralIntelligenceError,
    analyze_candidate_behavior,
)
from app.services.candidate_vessels import (
    AisValidationFailureError,
    CandidateVesselError,
    generate_candidate_vessels_for_spill,
    load_source_estimate_from_asset,
)
from app.services.trajectory_analysis import (
    TrajectoryAnalysisError,
    analyze_candidate_trajectories,
    load_candidate_result_from_asset,
)
from app.services.drift_modelling import (
    DriftModellingError,
    compute_drift_for_spill,
    extract_centroid,
)

from app.services.source_estimation import (
    SourceEstimationError,
    compute_source_estimate_for_spill,
)
from app.services.sentinel1_ingestion import Sentinel1IngestionError, ingest_sentinel1_artifact
from app.services.spill_detection import (
    AdaptiveThresholdSpillDetector,
    SpillDetectionError,
    detect_spills_from_sar_scene,
)
from app.models.evidence_fusion import EvidenceFusionRequest, EvidenceFusionResult
from app.services.evidence_fusion import EvidenceFusionError, fuse_evidence
from app.models.candidate_ranking import CandidateRanking, CandidateRankingRequest
from app.services.candidate_ranking import (
    CandidateRankingError,
    load_evidence_fusion_from_asset,
    rank_candidates,
)
from app.models.explainability import ExplainabilityReport, ExplainabilityRequest
from app.services.explainability import (
    ExplainabilityError,
    generate_explainability_report,
    load_candidate_ranking_from_asset,
)
from app.models.investigation_api import (
    ArtifactSummary,
    InvestigationCreateRequest,
    InvestigationListItem,
    InvestigationResponse,
    InvestigationRunRequest,
    InvestigationRunResponse,
    InvestigationStatusResponse,
)
from app.services.investigation_workflow import (
    default_investigation_store,
    list_investigation_artifacts,
    run_investigation_workflow,
)
from app.validation.schemas import ValidationResult

router = APIRouter()


class Sentinel1IngestRequest(BaseModel):
    artifact_path: str


class Sentinel1IngestResponse(BaseModel):
    scene: SatelliteScene
    validation: ValidationResult


class SpillDetectRequest(BaseModel):
    sar_asset_id: str | None = None
    sar_asset_path: str | None = None
    polarization: str | None = None
    damping_threshold_db: float | None = None
    k_sigma: float | None = None
    min_area_m2: float | None = None


class SpillDetectResponse(BaseModel):
    spill_detection: SpillDetection
    asset_id: str


def validate_safe_data_path(target_path_str: str, data_dir: Path) -> Path:
    """Validate that target_path resolves strictly within data_dir.

    Resolves symlinks/relative segments and validates containment using
    Path.is_relative_to(). Fails closed with HTTP 400 if outside.
    """
    try:
        candidate = Path(target_path_str)
        allowed_root = data_dir.resolve()
        if not candidate.is_absolute():
            resolved = (allowed_root / candidate).resolve()
        else:
            resolved = candidate.resolve()
        if not resolved.is_relative_to(allowed_root):
            raise HTTPException(
                status_code=400,
                detail=f"Path '{target_path_str}' is outside the allowed data directory.",
            )
        return resolved
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid path '{target_path_str}': {exc}",
        )


@router.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": settings.service_name,
        "store_investigation_count": len(default_investigation_store.list()),
    }


@router.post(
    "/api/v1/investigations/{investigation_id}/scenes/sentinel1/ingest",
    response_model=Sentinel1IngestResponse,
)
def ingest_sentinel1_scene(
    investigation_id: str,
    payload: Sentinel1IngestRequest,
) -> Sentinel1IngestResponse:
    if not payload.artifact_path or not payload.artifact_path.strip():
        raise HTTPException(status_code=400, detail="artifact_path is required")

    try:
        scene, validation_result, _asset = ingest_sentinel1_artifact(
            investigation_id=investigation_id,
            artifact_path=payload.artifact_path,
        )
        return Sentinel1IngestResponse(scene=scene, validation=validation_result)
    except Sentinel1IngestionError as exc:
        err_msg = str(exc)
        if "does not exist" in err_msg or "is not a file" in err_msg:
            raise HTTPException(status_code=404, detail=err_msg)
        raise HTTPException(status_code=422, detail=err_msg)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Sentinel-1 scene ingestion failed due to an internal error")


@router.post(
    "/api/v1/investigations/{investigation_id}/scenes/{scene_id}/spill-detect",
    response_model=SpillDetectResponse,
)
def detect_spill_scene(
    investigation_id: str,
    scene_id: str,
    payload: SpillDetectRequest,
) -> SpillDetectResponse:
    # 1. Resolve SAR asset
    sar_asset: Asset | None = None
    if payload.sar_asset_id:
        try:
            sar_asset = default_asset_registry.get(payload.sar_asset_id)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"SAR asset '{payload.sar_asset_id}' not found in registry",
            )
    elif payload.sar_asset_path:
        safe_path = validate_safe_data_path(payload.sar_asset_path, settings.data_dir)
        if not safe_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"SAR asset path '{payload.sar_asset_path}' does not exist",
            )
        sar_asset = Asset(
            id=f"asset-sar-{scene_id}",
            investigation_id=investigation_id,
            type=AssetType.IMAGERY_PREVIEW,
            provider="sentinel1",
            source="filesystem",
            location=str(safe_path),
            provenance=Provenance(product_id=scene_id),
        )
    else:
        # Search registered assets for investigation matching scene_id
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            if (
                asset.metadata.get("parent_scene_id") == scene_id
                or asset.provenance.extra.get("parent_scene_id") == scene_id
                or asset.provenance.product_id == scene_id
            ):
                sar_asset = asset
                break

    if sar_asset is None:
        raise HTTPException(
            status_code=400,
            detail="Either sar_asset_id or sar_asset_path must be provided if not already registered",
        )

    # 2. Reconstruct scene reference
    acq_time = sar_asset.acquisition_time or sar_asset.provenance.retrieved_at or datetime.now(timezone.utc)
    scene = SatelliteScene(
        id=scene_id,
        investigation_id=investigation_id,
        asset_id=sar_asset.id,
        provider="sentinel1",
        sensor="SENTINEL-1 SAR",
        acquisition_time=acq_time,
        footprint=BBoxAreaOfInterest(
            kind="bbox",
            bbox=BoundingBox(west=-180.0, south=-90.0, east=180.0, north=90.0),
        ),
    )

    # 3. Configure detector
    detector_kwargs: dict[str, Any] = {}
    if payload.damping_threshold_db is not None:
        detector_kwargs["damping_threshold_db"] = payload.damping_threshold_db
    if payload.k_sigma is not None:
        detector_kwargs["k_sigma"] = payload.k_sigma

    detector = AdaptiveThresholdSpillDetector(**detector_kwargs) if detector_kwargs else None

    service_kwargs: dict[str, Any] = {}
    if payload.min_area_m2 is not None:
        service_kwargs["min_area_m2"] = payload.min_area_m2

    try:
        spill_detection, derived_asset = detect_spills_from_sar_scene(
            investigation_id=investigation_id,
            scene=scene,
            sar_asset=sar_asset,
            detector=detector,
            polarization=payload.polarization,
            **service_kwargs,
        )
        return SpillDetectResponse(
            spill_detection=spill_detection,
            asset_id=derived_asset.id,
        )
    except SpillDetectionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Spill detection failed due to an internal error: {exc}",
        )


class EnvironmentalAcquireRequest(BaseModel):
    scene_id: str | None = None
    area_of_interest: AreaOfInterest | None = None
    time_window: TimeWindow | None = None
    providers: list[str] = ["era5", "cmems"]
    spatial_buffer_degrees: float = 0.25
    lookback_hours: float = 24.0
    forward_hours: float = 6.0


class EnvironmentalProviderItemResponse(BaseModel):
    provider_id: str
    status: str
    asset_id: str | None = None
    environment_id: str | None = None
    error: str | None = None
    validation_classification: str | None = None


class EnvironmentalAcquireResponse(BaseModel):
    investigation_id: str
    scene_id: str | None = None
    succeeded_providers: list[str]
    failed_providers: list[str]
    items: dict[str, EnvironmentalProviderItemResponse]


@router.post(
    "/api/v1/investigations/{investigation_id}/environment/acquire",
    response_model=EnvironmentalAcquireResponse,
)
def acquire_environment(
    investigation_id: str,
    payload: EnvironmentalAcquireRequest,
) -> EnvironmentalAcquireResponse:
    from app.services.environmental_acquisition import (
        EnvironmentalAcquisitionError,
        acquire_environmental_data_for_investigation,
        acquire_environmental_data_for_scene,
    )

    # 1. Resolve spatial and temporal context
    aoi = payload.area_of_interest
    window = payload.time_window

    if aoi is None or window is None:
        if not payload.scene_id:
            raise HTTPException(
                status_code=400,
                detail="Either scene_id or both area_of_interest and time_window must be provided",
            )
        # Search registered assets for scene
        matched_asset = None
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            if (
                asset.metadata.get("parent_scene_id") == payload.scene_id
                or asset.provenance.extra.get("parent_scene_id") == payload.scene_id
                or asset.provenance.product_id == payload.scene_id
            ):
                matched_asset = asset
                break

        if matched_asset is None:
            raise HTTPException(
                status_code=404,
                detail=f"Scene '{payload.scene_id}' not found for investigation '{investigation_id}'",
            )

        acq_time = matched_asset.acquisition_time or matched_asset.provenance.retrieved_at or datetime.now(timezone.utc)
        scene = SatelliteScene(
            id=payload.scene_id,
            investigation_id=investigation_id,
            asset_id=matched_asset.id,
            provider="sentinel1",
            sensor="SENTINEL-1 SAR",
            acquisition_time=acq_time,
            footprint=BBoxAreaOfInterest(
                kind="bbox",
                bbox=BoundingBox(west=-180.0, south=-90.0, east=180.0, north=90.0),
            ),
        )

        try:
            summary = acquire_environmental_data_for_scene(
                scene=scene,
                investigation_id=investigation_id,
                providers=payload.providers,
                spatial_buffer_degrees=payload.spatial_buffer_degrees,
                lookback_hours=payload.lookback_hours,
                forward_hours=payload.forward_hours,
            )
        except EnvironmentalAcquisitionError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Environmental acquisition failed: {exc}")
    else:
        try:
            summary = acquire_environmental_data_for_investigation(
                investigation_id=investigation_id,
                area_of_interest=aoi,
                time_window=window,
                scene_id=payload.scene_id,
                providers=payload.providers,
                spatial_buffer_degrees=payload.spatial_buffer_degrees,
                lookback_hours=payload.lookback_hours,
                forward_hours=payload.forward_hours,
                apply_framing=True,
            )
        except EnvironmentalAcquisitionError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Environmental acquisition failed: {exc}")

    # Build response items
    items_response: dict[str, EnvironmentalProviderItemResponse] = {}
    for pid, item in summary.items.items():
        val_class = item.validation.metadata.get("validation_classification") if item.validation else None
        items_response[pid] = EnvironmentalProviderItemResponse(
            provider_id=pid,
            status=item.status.value,
            asset_id=item.asset.id if item.asset else None,
            environment_id=item.environment.id if item.environment else None,
            error=item.error,
            validation_classification=val_class,
        )

    return EnvironmentalAcquireResponse(
        investigation_id=investigation_id,
        scene_id=payload.scene_id,
        succeeded_providers=summary.succeeded_providers,
        failed_providers=summary.failed_providers,
        items=items_response,
    )


class DriftModellingRequest(BaseModel):
    wind_asset_id: str
    current_asset_id: str
    drift_hours: float | None = None
    step_hours: float | None = None
    leeway_fraction: float | None = None


@router.post(
    "/api/v1/investigations/{investigation_id}/spills/{spill_id}/drift",
    response_model=DriftResult,
)
def run_forward_drift_endpoint(
    investigation_id: str,
    spill_id: str,
    payload: DriftModellingRequest,
) -> DriftResult:
    # 1. Resolve spill asset
    spill_asset: Asset | None = None
    try:
        candidate = default_asset_registry.get(spill_id)
        if candidate.investigation_id == investigation_id:
            spill_asset = candidate
    except KeyError:
        pass

    if spill_asset is None:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            if (
                asset.id == spill_id
                or asset.provenance.product_id == spill_id
                or asset.metadata.get("detection_id") == spill_id
                or asset.metadata.get("parent_scene_id") == spill_id
                or asset.provenance.extra.get("detection_id") == spill_id
            ):
                spill_asset = asset
                break

    if spill_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Spill asset or detection '{spill_id}' not found for investigation '{investigation_id}'",
        )

    # 2. Extract centroid and validate positive detection
    centroid_dict = spill_asset.metadata.get("centroid")
    detected = spill_asset.metadata.get("detected")
    if detected is None:
        detected = bool(centroid_dict is not None and spill_asset.metadata.get("spill_count", 1) > 0)

    spill_detection = SpillDetection(
        id=spill_asset.provenance.product_id or spill_id,
        investigation_id=investigation_id,
        asset_id=spill_asset.id,
        scene_id=spill_asset.provenance.extra.get("parent_scene_id") or spill_asset.metadata.get("parent_scene_id") or "unknown-scene",
        detected=detected,
        confidence=spill_asset.metadata.get("confidence", 1.0 if detected else 0.0),
        geometry=spill_asset.metadata.get("geometry", {}),
        metadata=spill_asset.metadata,
    )

    try:
        origin_lon, origin_lat = extract_centroid(spill_detection)
    except DriftModellingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # 3. Resolve wind and current assets
    try:
        wind_asset = default_asset_registry.get(payload.wind_asset_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Wind asset '{payload.wind_asset_id}' not found in registry",
        )

    try:
        current_asset = default_asset_registry.get(payload.current_asset_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Current asset '{payload.current_asset_id}' not found in registry",
        )

    # 4. Compute drift
    drift_kwargs: dict[str, Any] = {}
    if payload.drift_hours is not None:
        drift_kwargs["drift_hours"] = payload.drift_hours
    if payload.step_hours is not None:
        drift_kwargs["step_hours"] = payload.step_hours
    if payload.leeway_fraction is not None:
        drift_kwargs["leeway_fraction"] = payload.leeway_fraction

    obs_time = (
        spill_asset.acquisition_time
        or spill_asset.provenance.retrieved_at
        or datetime.now(timezone.utc)
    )

    try:
        drift_result, _ = compute_drift_for_spill(
            investigation_id=investigation_id,
            spill_detection_id=spill_detection.id,
            origin_lon=origin_lon,
            origin_lat=origin_lat,
            observation_time=obs_time,
            wind_asset=wind_asset,
            current_asset=current_asset,
            **drift_kwargs,
        )
        return drift_result
    except DriftModellingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Drift modelling failed: {exc}")


class SourceEstimateRequest(BaseModel):
    wind_asset_id: str
    current_asset_id: str
    lookback_hours: float | None = None
    step_hours: float | None = None
    leeway_fraction: float | None = None
    uncertainty_growth_rate_m_per_h: float | None = None


@router.post(
    "/api/v1/investigations/{investigation_id}/spills/{spill_id}/source-estimate",
    response_model=SourceEstimateResult,
)
def run_source_estimate_endpoint(
    investigation_id: str,
    spill_id: str,
    payload: SourceEstimateRequest,
) -> SourceEstimateResult:
    # 1. Resolve spill asset
    spill_asset: Asset | None = None
    try:
        candidate = default_asset_registry.get(spill_id)
        if candidate.investigation_id == investigation_id:
            spill_asset = candidate
    except KeyError:
        pass

    if spill_asset is None:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            if (
                asset.id == spill_id
                or asset.provenance.product_id == spill_id
                or asset.metadata.get("detection_id") == spill_id
                or asset.metadata.get("parent_scene_id") == spill_id
                or asset.provenance.extra.get("detection_id") == spill_id
            ):
                spill_asset = asset
                break

    if spill_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Spill asset or detection '{spill_id}' not found for investigation '{investigation_id}'",
        )

    # 2. Extract centroid and validate positive detection
    centroid_dict = spill_asset.metadata.get("centroid")
    detected = spill_asset.metadata.get("detected")
    if detected is None:
        detected = bool(centroid_dict is not None and spill_asset.metadata.get("spill_count", 1) > 0)

    spill_detection = SpillDetection(
        id=spill_asset.provenance.product_id or spill_id,
        investigation_id=investigation_id,
        asset_id=spill_asset.id,
        scene_id=spill_asset.provenance.extra.get("parent_scene_id") or spill_asset.metadata.get("parent_scene_id") or "unknown-scene",
        detected=detected,
        confidence=spill_asset.metadata.get("confidence", 1.0 if detected else 0.0),
        geometry=spill_asset.metadata.get("geometry", {}),
        metadata=spill_asset.metadata,
    )

    try:
        origin_lon, origin_lat = extract_centroid(spill_detection)
    except DriftModellingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # 3. Resolve wind and current assets
    try:
        wind_asset = default_asset_registry.get(payload.wind_asset_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Wind asset '{payload.wind_asset_id}' not found in registry",
        )

    try:
        current_asset = default_asset_registry.get(payload.current_asset_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Current asset '{payload.current_asset_id}' not found in registry",
        )

    # 4. Compute source estimate
    source_kwargs: dict[str, Any] = {}
    if payload.lookback_hours is not None:
        source_kwargs["lookback_hours"] = payload.lookback_hours
    if payload.step_hours is not None:
        source_kwargs["step_hours"] = payload.step_hours
    if payload.leeway_fraction is not None:
        source_kwargs["leeway_fraction"] = payload.leeway_fraction
    if payload.uncertainty_growth_rate_m_per_h is not None:
        source_kwargs["uncertainty_growth_m_per_h"] = payload.uncertainty_growth_rate_m_per_h

    # Extract spill area if available
    spill_area = spill_asset.metadata.get("area") or spill_asset.metadata.get("total_area_m2")
    if spill_area is not None:
        try:
            source_kwargs["spill_area_m2"] = float(spill_area)
        except (ValueError, TypeError):
            pass

    obs_time = (
        spill_asset.acquisition_time
        or spill_asset.provenance.retrieved_at
        or datetime.now(timezone.utc)
    )

    try:
        source_result, _ = compute_source_estimate_for_spill(
            investigation_id=investigation_id,
            spill_detection_id=spill_detection.id,
            origin_lon=origin_lon,
            origin_lat=origin_lat,
            observation_time=obs_time,
            wind_asset=wind_asset,
            current_asset=current_asset,
            **source_kwargs,
        )
        return source_result
    except SourceEstimationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Source estimation failed: {exc}")


class CandidateVesselRequest(BaseModel):
    source_estimate_id: str
    ais_asset_id: str | None = None
    temporal_window_hours: float | None = None
    spatial_buffer_km: float | None = None


@router.post(
    "/api/v1/investigations/{investigation_id}/spills/{spill_id}/candidates",
    response_model=CandidateVesselGenerationResult,
)
def generate_candidates_endpoint(
    investigation_id: str,
    spill_id: str,
    payload: CandidateVesselRequest,
) -> CandidateVesselGenerationResult:
    # 1. Resolve spill asset
    spill_asset: Asset | None = None
    try:
        candidate = default_asset_registry.get(spill_id)
        if candidate.investigation_id == investigation_id:
            spill_asset = candidate
    except KeyError:
        pass

    if spill_asset is None:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            if (
                asset.id == spill_id
                or asset.provenance.product_id == spill_id
                or asset.metadata.get("detection_id") == spill_id
                or asset.metadata.get("parent_scene_id") == spill_id
                or asset.provenance.extra.get("detection_id") == spill_id
            ):
                spill_asset = asset
                break

    if spill_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Spill asset or detection '{spill_id}' not found for investigation '{investigation_id}'",
        )

    # 2. Resolve source estimate asset
    source_asset: Asset | None = None
    try:
        candidate_source = default_asset_registry.get(payload.source_estimate_id)
        if candidate_source.investigation_id == investigation_id:
            source_asset = candidate_source
    except KeyError:
        pass

    if source_asset is None:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            if (
                asset.id == payload.source_estimate_id
                or asset.provenance.product_id == payload.source_estimate_id
                or asset.provenance.extra.get("source_estimate_id") == payload.source_estimate_id
                or asset.provenance.extra.get("source_id") == payload.source_estimate_id
                or asset.metadata.get("source_estimate_id") == payload.source_estimate_id
                or (
                    asset.type == AssetType.DRIFT_PRODUCT
                    and payload.source_estimate_id in str(asset.location)
                )
            ):
                source_asset = asset
                break

    if source_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Source estimate '{payload.source_estimate_id}' not found for investigation '{investigation_id}'",
        )

    try:
        source_estimate = load_source_estimate_from_asset(source_asset)
    except CandidateVesselError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # 3. Check explicit ais_asset_id if provided
    if payload.ais_asset_id:
        try:
            ais_asset = default_asset_registry.get(payload.ais_asset_id)
            if ais_asset.investigation_id != investigation_id:
                raise HTTPException(
                    status_code=404,
                    detail=f"AIS asset '{payload.ais_asset_id}' does not belong to investigation '{investigation_id}'",
                )
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"AIS asset '{payload.ais_asset_id}' not found in registry",
            )

    # 4. Invoke candidate generation service
    cand_kwargs: dict[str, Any] = {}
    if payload.temporal_window_hours is not None:
        cand_kwargs["temporal_window_hours"] = payload.temporal_window_hours
    if payload.spatial_buffer_km is not None:
        cand_kwargs["spatial_buffer_km"] = payload.spatial_buffer_km

    try:
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=investigation_id,
            spill_id=spill_id,
            source_estimate=source_estimate,
            ais_asset_id=payload.ais_asset_id,
            **cand_kwargs,
        )
        return result
    except AisValidationFailureError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except CandidateVesselError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Candidate vessel generation failed: {exc}")


class TrajectoryAnalysisRequest(BaseModel):
    source_estimate_id: str
    candidate_generation_id: str | None = None


@router.post(
    "/api/v1/investigations/{investigation_id}/spills/{spill_id}/trajectory-analysis",
    response_model=TrajectoryAnalysisResult,
)
def trajectory_analysis_endpoint(
    investigation_id: str,
    spill_id: str,
    payload: TrajectoryAnalysisRequest,
) -> TrajectoryAnalysisResult:
    # 1. Resolve spill asset
    spill_asset: Asset | None = None
    try:
        candidate = default_asset_registry.get(spill_id)
        if candidate.investigation_id == investigation_id:
            spill_asset = candidate
    except KeyError:
        pass

    if spill_asset is None:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            if (
                asset.id == spill_id
                or asset.provenance.product_id == spill_id
                or asset.metadata.get("detection_id") == spill_id
                or asset.metadata.get("parent_scene_id") == spill_id
                or asset.provenance.extra.get("detection_id") == spill_id
            ):
                spill_asset = asset
                break

    if spill_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Spill asset or detection '{spill_id}' not found for investigation '{investigation_id}'",
        )

    # 2. Resolve source estimate asset
    source_asset: Asset | None = None
    try:
        candidate_source = default_asset_registry.get(payload.source_estimate_id)
        if candidate_source.investigation_id == investigation_id:
            source_asset = candidate_source
    except KeyError:
        pass

    if source_asset is None:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            if (
                asset.id == payload.source_estimate_id
                or asset.provenance.product_id == payload.source_estimate_id
                or asset.provenance.extra.get("source_estimate_id") == payload.source_estimate_id
                or asset.provenance.extra.get("source_id") == payload.source_estimate_id
                or asset.metadata.get("source_estimate_id") == payload.source_estimate_id
                or (
                    asset.type == AssetType.DRIFT_PRODUCT
                    and payload.source_estimate_id in str(asset.location)
                )
            ):
                source_asset = asset
                break

    if source_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Source estimate '{payload.source_estimate_id}' not found for investigation '{investigation_id}'",
        )

    try:
        source_estimate = load_source_estimate_from_asset(source_asset)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # 3. Resolve candidate generation asset
    candidate_asset: Asset | None = None
    if payload.candidate_generation_id:
        try:
            cand = default_asset_registry.get(payload.candidate_generation_id)
            if cand.investigation_id == investigation_id:
                candidate_asset = cand
        except KeyError:
            pass

        if candidate_asset is None:
            for asset in default_asset_registry.list_for_investigation(investigation_id):
                if (
                    asset.id == payload.candidate_generation_id
                    or asset.provenance.product_id == payload.candidate_generation_id
                    or asset.provenance.extra.get("candidate_generation_id") == payload.candidate_generation_id
                    or asset.metadata.get("candidate_generation_id") == payload.candidate_generation_id
                    or (
                        asset.type == AssetType.DOCUMENT
                        and payload.candidate_generation_id in str(asset.location)
                    )
                ):
                    candidate_asset = asset
                    break

        if candidate_asset is None:
            raise HTTPException(
                status_code=404,
                detail=f"Candidate generation asset '{payload.candidate_generation_id}' not found for investigation '{investigation_id}'",
            )
    else:
        # Auto-discover candidate asset for this investigation and spill
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            extra = asset.provenance.extra or {}
            if (
                asset.type == AssetType.DOCUMENT
                and (extra.get("asset_type") == "candidate_vessels" or asset.metadata.get("asset_type") == "candidate_vessels")
                and (extra.get("spill_detection_id") == spill_id or asset.metadata.get("spill_id") == spill_id)
            ):
                candidate_asset = asset
                break

        if candidate_asset is None:
            raise HTTPException(
                status_code=404,
                detail=f"Candidate vessel generation result not found for investigation '{investigation_id}' and spill '{spill_id}'",
            )

    try:
        candidate_result = load_candidate_result_from_asset(candidate_asset, default_asset_registry)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # 4. Invoke trajectory analysis service
    try:
        result, _ = analyze_candidate_trajectories(
            investigation_id=investigation_id,
            spill_id=spill_id,
            source_estimate=source_estimate,
            candidate_result=candidate_result,
        )
        return result
    except TrajectoryAnalysisError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Trajectory analysis failed: {exc}")


@router.post(
    "/api/v1/investigations/{investigation_id}/spills/{spill_id}/behavioral-intelligence",
    response_model=BehavioralIntelligenceResult,
)
def behavioral_intelligence_endpoint(
    investigation_id: str,
    spill_id: str,
    payload: BehavioralIntelligenceRequest,
) -> BehavioralIntelligenceResult:
    # 1. Resolve spill asset
    spill_asset: Asset | None = None
    try:
        candidate = default_asset_registry.get(spill_id)
        if candidate.investigation_id == investigation_id:
            spill_asset = candidate
    except KeyError:
        pass

    if spill_asset is None:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            if (
                asset.id == spill_id
                or asset.provenance.product_id == spill_id
                or asset.metadata.get("detection_id") == spill_id
                or asset.metadata.get("parent_scene_id") == spill_id
                or asset.provenance.extra.get("detection_id") == spill_id
            ):
                spill_asset = asset
                break

    if spill_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Spill asset or detection '{spill_id}' not found for investigation '{investigation_id}'",
        )

    # 2. Resolve source estimate asset
    source_asset: Asset | None = None
    try:
        candidate_source = default_asset_registry.get(payload.source_estimate_id)
        if candidate_source.investigation_id == investigation_id:
            source_asset = candidate_source
    except KeyError:
        pass

    if source_asset is None:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            if (
                asset.id == payload.source_estimate_id
                or asset.provenance.product_id == payload.source_estimate_id
                or asset.provenance.extra.get("source_estimate_id") == payload.source_estimate_id
                or asset.provenance.extra.get("source_id") == payload.source_estimate_id
                or asset.metadata.get("source_estimate_id") == payload.source_estimate_id
                or (
                    asset.type == AssetType.DRIFT_PRODUCT
                    and payload.source_estimate_id in str(asset.location)
                )
            ):
                source_asset = asset
                break

    if source_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Source estimate '{payload.source_estimate_id}' not found for investigation '{investigation_id}'",
        )

    try:
        source_estimate = load_source_estimate_from_asset(source_asset)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # 3. Resolve candidate generation asset
    candidate_asset: Asset | None = None
    if payload.candidate_generation_id:
        try:
            cand = default_asset_registry.get(payload.candidate_generation_id)
            if cand.investigation_id == investigation_id:
                candidate_asset = cand
        except KeyError:
            pass

        if candidate_asset is None:
            for asset in default_asset_registry.list_for_investigation(investigation_id):
                if (
                    asset.id == payload.candidate_generation_id
                    or asset.provenance.product_id == payload.candidate_generation_id
                    or asset.provenance.extra.get("candidate_generation_id") == payload.candidate_generation_id
                    or asset.metadata.get("candidate_generation_id") == payload.candidate_generation_id
                    or (
                        asset.type == AssetType.DOCUMENT
                        and payload.candidate_generation_id in str(asset.location)
                    )
                ):
                    candidate_asset = asset
                    break

        if candidate_asset is None:
            raise HTTPException(
                status_code=404,
                detail=f"Candidate generation asset '{payload.candidate_generation_id}' not found for investigation '{investigation_id}'",
            )
    else:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            extra = asset.provenance.extra or {}
            if (
                asset.type == AssetType.DOCUMENT
                and (extra.get("asset_type") == "candidate_vessels" or asset.metadata.get("asset_type") == "candidate_vessels")
                and (extra.get("spill_detection_id") == spill_id or asset.metadata.get("spill_id") == spill_id)
            ):
                candidate_asset = asset
                break

        if candidate_asset is None:
            raise HTTPException(
                status_code=404,
                detail=f"Candidate vessel generation result not found for investigation '{investigation_id}' and spill '{spill_id}'",
            )

    try:
        candidate_result = load_candidate_result_from_asset(candidate_asset, default_asset_registry)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # 4. Invoke behavioural intelligence service
    try:
        result, _ = analyze_candidate_behavior(
            investigation_id=investigation_id,
            spill_id=spill_id,
            source_estimate=source_estimate,
            candidate_result=candidate_result,
            trajectory_analysis_id=payload.trajectory_analysis_id,
            speed_drop_threshold_knots=payload.speed_drop_threshold_knots,
            loitering_speed_threshold_knots=payload.loitering_speed_threshold_knots,
            course_alteration_threshold_deg=payload.course_alteration_threshold_deg,
            transmission_gap_threshold_seconds=payload.transmission_gap_threshold_seconds,
        )
        return result
    except BehavioralIntelligenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Behavioral intelligence analysis failed: {exc}")


@router.post(
    "/api/v1/investigations/{investigation_id}/spills/{spill_id}/evidence-fusion",
    response_model=EvidenceFusionResult,
)
def evidence_fusion_endpoint(
    investigation_id: str,
    spill_id: str,
    payload: EvidenceFusionRequest,
) -> EvidenceFusionResult:
    """Stage F1 — Multi-Source Spatio-Temporal Evidence Fusion.

    Fuses physical evidence from B3/D3/E1/E2 with contextual intelligence from E3
    into a deterministic composite concordance score per candidate vessel.

    Invariants:
    - E3 behavioural anomalies contribute exactly 0.00 to the primary concordance score.
    - Optional D1 forward drift is an excluded cross-check; omitting it does not change the score.
    - Candidate order from E1 is strictly preserved (no ranking in F1).
    - Concordance score is NOT a probability, guilt score, or legal evidence.
    """
    # 1. Resolve spill asset
    spill_asset: Asset | None = None
    try:
        s_candidate = default_asset_registry.get(spill_id)
        if s_candidate.investigation_id == investigation_id:
            spill_asset = s_candidate
    except KeyError:
        pass

    if spill_asset is None:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            if (
                asset.id == spill_id
                or asset.provenance.product_id == spill_id
                or asset.metadata.get("detection_id") == spill_id
                or asset.metadata.get("parent_scene_id") == spill_id
                or asset.provenance.extra.get("detection_id") == spill_id
            ):
                spill_asset = asset
                break

    if spill_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Spill asset or detection '{spill_id}' not found for investigation '{investigation_id}'",
        )

    # 2. Resolve source estimate asset
    source_asset: Asset | None = None
    try:
        se_candidate = default_asset_registry.get(payload.source_estimate_id)
        if se_candidate.investigation_id == investigation_id:
            source_asset = se_candidate
    except KeyError:
        pass

    if source_asset is None:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            if (
                asset.id == payload.source_estimate_id
                or asset.provenance.product_id == payload.source_estimate_id
                or asset.provenance.extra.get("source_estimate_id") == payload.source_estimate_id
                or asset.metadata.get("source_estimate_id") == payload.source_estimate_id
                or (
                    asset.type == AssetType.DRIFT_PRODUCT
                    and payload.source_estimate_id in str(asset.location)
                )
            ):
                source_asset = asset
                break

    if source_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Source estimate '{payload.source_estimate_id}' not found for investigation '{investigation_id}'",
        )

    try:
        source_estimate = load_source_estimate_from_asset(source_asset)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # 3. Resolve candidate generation asset
    candidate_asset: Asset | None = None
    try:
        cg_candidate = default_asset_registry.get(payload.candidate_generation_id)
        if cg_candidate.investigation_id == investigation_id:
            candidate_asset = cg_candidate
    except KeyError:
        pass

    if candidate_asset is None:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            extra = asset.provenance.extra or {}
            if (
                asset.id == payload.candidate_generation_id
                or asset.provenance.product_id == payload.candidate_generation_id
                or extra.get("candidate_generation_id") == payload.candidate_generation_id
                or asset.metadata.get("candidate_generation_id") == payload.candidate_generation_id
                or (asset.type == AssetType.DOCUMENT and payload.candidate_generation_id in str(asset.location))
            ):
                candidate_asset = asset
                break

    if candidate_asset is None:
        # Auto-discover by asset_type tag
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            extra = asset.provenance.extra or {}
            if (
                asset.type == AssetType.DOCUMENT
                and (extra.get("asset_type") == "candidate_vessels" or asset.metadata.get("asset_type") == "candidate_vessels")
                and (extra.get("spill_detection_id") == spill_id or asset.metadata.get("spill_id") == spill_id)
            ):
                candidate_asset = asset
                break

    if candidate_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Candidate generation asset '{payload.candidate_generation_id}' not found for investigation '{investigation_id}'",
        )

    try:
        candidate_result = load_candidate_result_from_asset(candidate_asset, default_asset_registry)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # 4. Resolve trajectory analysis asset
    trajectory_asset: Asset | None = None
    try:
        ta_candidate = default_asset_registry.get(payload.trajectory_analysis_id)
        if ta_candidate.investigation_id == investigation_id:
            trajectory_asset = ta_candidate
    except KeyError:
        pass

    if trajectory_asset is None:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            extra = asset.provenance.extra or {}
            if (
                asset.id == payload.trajectory_analysis_id
                or asset.provenance.product_id == payload.trajectory_analysis_id
                or (
                    extra.get("asset_type") == "trajectory_analysis"
                    and (extra.get("spill_detection_id") == spill_id or asset.metadata.get("spill_id") == spill_id)
                )
            ):
                trajectory_asset = asset
                break

    if trajectory_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Trajectory analysis asset '{payload.trajectory_analysis_id}' not found for investigation '{investigation_id}'",
        )

    # Build TrajectoryAnalysisResult from registered asset metadata
    try:
        from app.models.trajectory_analysis import TrajectoryAnalysisResult as _TAR
        t_extra = trajectory_asset.provenance.extra or {}
        trajectory_result = _TAR(
            id=trajectory_asset.provenance.product_id or trajectory_asset.id,
            investigation_id=investigation_id,
            spill_detection_id=t_extra.get("spill_detection_id", spill_id),
            source_estimate_id=t_extra.get("source_estimate_id", source_estimate.id),
            candidate_generation_id=t_extra.get("candidate_generation_id", candidate_result.id),
            derived_asset_id=trajectory_asset.id,
            analyzed_vessel_count=int(t_extra.get("analyzed_vessel_count", 0)),
            analyses=[],
            metadata={},
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to load trajectory analysis: {exc}")

    # 5. Resolve behavioral intelligence asset
    behavioral_asset: Asset | None = None
    try:
        bi_candidate = default_asset_registry.get(payload.behavioral_intelligence_id)
        if bi_candidate.investigation_id == investigation_id:
            behavioral_asset = bi_candidate
    except KeyError:
        pass

    if behavioral_asset is None:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            extra = asset.provenance.extra or {}
            if (
                asset.id == payload.behavioral_intelligence_id
                or asset.provenance.product_id == payload.behavioral_intelligence_id
                or (
                    extra.get("asset_type") == "behavioral_intelligence"
                    and (extra.get("spill_detection_id") == spill_id or asset.metadata.get("spill_id") == spill_id)
                )
            ):
                behavioral_asset = asset
                break

    if behavioral_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Behavioral intelligence asset '{payload.behavioral_intelligence_id}' not found for investigation '{investigation_id}'",
        )

    try:
        from app.models.behavioral_intelligence import BehavioralIntelligenceResult as _BIR
        b_extra = behavioral_asset.provenance.extra or {}
        behavioral_result = _BIR(
            id=behavioral_asset.provenance.product_id or behavioral_asset.id,
            investigation_id=investigation_id,
            spill_detection_id=b_extra.get("spill_detection_id", spill_id),
            source_estimate_id=b_extra.get("source_estimate_id", source_estimate.id),
            candidate_generation_id=b_extra.get("candidate_generation_id", candidate_result.id),
            trajectory_analysis_id=b_extra.get("trajectory_analysis_id"),
            derived_asset_id=behavioral_asset.id,
            analyzed_vessel_count=int(b_extra.get("analyzed_vessel_count", 0)),
            profiles=[],
            total_anomalies_detected=int(b_extra.get("total_anomalies_detected", 0)),
            total_transmission_gaps_detected=int(b_extra.get("total_transmission_gaps_detected", 0)),
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to load behavioral intelligence: {exc}")

    # 6. Execute fusion (no forward drift in API route — D1 cross-check is excluded from score anyway)
    try:
        result, _ = fuse_evidence(
            investigation_id=investigation_id,
            spill_id=spill_id,
            source_estimate=source_estimate,
            candidate_result=candidate_result,
            trajectory_result=trajectory_result,
            behavioral_result=behavioral_result,
            drift_result=None,
        )
        return result
    except EvidenceFusionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Evidence fusion failed: {exc}")


@router.post(
    "/api/v1/investigations/{investigation_id}/spills/{spill_id}/candidate-ranking",
    response_model=CandidateRanking,
)
def candidate_ranking_endpoint(
    investigation_id: str,
    spill_id: str,
    payload: CandidateRankingRequest | None = None,
) -> CandidateRanking:
    """Stage F2 — Candidate Scoring & Ranking.

    Consumes Stage F1 Evidence Fusion output and produces a deterministic comparative
    ranking of candidate vessels based on physical evidence consistency.

    Invariants:
    - Primary score uses spatial (0.50), temporal (0.25), trajectory (0.25) channels only.
    - Missing channels are excluded from the denominator (never treated as zero).
    - E3 behavioral anomalies contribute exactly 0.00 to the score.
    - D1 forward drift contributes exactly 0.00 to the score.
    - Deterministic tie-breaking:
      1. Higher evidence availability ratio
      2. Smaller spatial discrepancy
      3. Smaller temporal discrepancy
      4. Stable vessel identifier
    - Score is a physical compatibility index in [0, 1], NOT a probability of guilt.
    """
    req_payload = payload or CandidateRankingRequest()

    # 1. Validate investigation and spill context
    spill_asset: Asset | None = None
    try:
        s_candidate = default_asset_registry.get(spill_id)
        if s_candidate.investigation_id == investigation_id:
            spill_asset = s_candidate
    except KeyError:
        pass

    if spill_asset is None:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            if (
                asset.id == spill_id
                or asset.provenance.product_id == spill_id
                or asset.metadata.get("detection_id") == spill_id
                or asset.metadata.get("spill_id") == spill_id
                or asset.provenance.extra.get("spill_detection_id") == spill_id
            ):
                spill_asset = asset
                break

    if spill_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Spill asset or detection '{spill_id}' not found for investigation '{investigation_id}'",
        )

    # 2. Resolve Stage F1 evidence fusion asset
    fusion_asset: Asset | None = None
    if req_payload.evidence_fusion_id:
        try:
            f_candidate = default_asset_registry.get(req_payload.evidence_fusion_id)
            if f_candidate.investigation_id == investigation_id:
                fusion_asset = f_candidate
        except KeyError:
            pass

        if fusion_asset is None:
            for asset in default_asset_registry.list_for_investigation(investigation_id):
                extra = asset.provenance.extra or {}
                if (
                    asset.id == req_payload.evidence_fusion_id
                    or asset.provenance.product_id == req_payload.evidence_fusion_id
                    or extra.get("evidence_fusion_id") == req_payload.evidence_fusion_id
                    or asset.metadata.get("evidence_fusion_id") == req_payload.evidence_fusion_id
                    or (asset.type == AssetType.DOCUMENT and req_payload.evidence_fusion_id in str(asset.location))
                ):
                    fusion_asset = asset
                    break

        if fusion_asset is None:
            raise HTTPException(
                status_code=404,
                detail=f"Evidence fusion asset '{req_payload.evidence_fusion_id}' not found for investigation '{investigation_id}'",
            )
    else:
        # Auto-discover latest evidence fusion asset
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            extra = asset.provenance.extra or {}
            if (
                asset.type == AssetType.DOCUMENT
                and (extra.get("asset_type") == "evidence_fusion" or asset.metadata.get("asset_type") == "evidence_fusion")
                and (extra.get("spill_detection_id") == spill_id or asset.metadata.get("spill_id") == spill_id)
            ):
                fusion_asset = asset
                break

        if fusion_asset is None:
            raise HTTPException(
                status_code=404,
                detail=f"Evidence fusion asset not found for investigation '{investigation_id}' and spill '{spill_id}'",
            )

    # 3. Load evidence fusion result
    try:
        evidence_fusion = load_evidence_fusion_from_asset(fusion_asset, default_asset_registry)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to load evidence fusion result: {exc}")

    # 4. Execute candidate scoring and ranking
    try:
        ranking, _ = rank_candidates(
            evidence_fusion=evidence_fusion,
            investigation_id=investigation_id,
            spill_id=spill_id,
        )
        return ranking
    except CandidateRankingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Candidate ranking failed: {exc}")


@router.post(
    "/api/v1/investigations/{investigation_id}/spills/{spill_id}/explainability",
    response_model=ExplainabilityReport,
)
def explainability_endpoint(
    investigation_id: str,
    spill_id: str,
    payload: ExplainabilityRequest | None = None,
) -> ExplainabilityReport:
    """Stage F3 — Explainability & Uncertainty.

    Consumes Stage F2 CandidateRanking output and produces a deterministic,
    investigator-readable ExplainabilityReport.

    Invariants:
    - F2 candidate ordering, ranks, and evidence_consistency_scores are strictly preserved.
    - F3 does NOT recalculate or modify scores or ranking.
    - Descriptive bands: HIGH (>=0.75), MODERATE (>=0.50), LOW (<0.50), INSUFFICIENT_DATA (None).
    - Communicates uncertainty through evidence availability and explicit limitations.
    - NO unsupported confidence percentages or intervals.
    - Contextual signals (behavioral intelligence, forward drift) contribute 0.00 to score.
    - Includes mandatory scientific disclaimer on attribution and legal boundaries.
    """
    req_payload = payload or ExplainabilityRequest()

    # 1. Validate investigation and spill context
    spill_asset: Asset | None = None
    try:
        s_candidate = default_asset_registry.get(spill_id)
        if s_candidate.investigation_id == investigation_id:
            spill_asset = s_candidate
    except KeyError:
        pass

    if spill_asset is None:
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            if (
                asset.id == spill_id
                or asset.provenance.product_id == spill_id
                or asset.metadata.get("detection_id") == spill_id
                or asset.metadata.get("spill_id") == spill_id
                or asset.provenance.extra.get("spill_detection_id") == spill_id
            ):
                spill_asset = asset
                break

    if spill_asset is None:
        raise HTTPException(
            status_code=404,
            detail=f"Spill asset or detection '{spill_id}' not found for investigation '{investigation_id}'",
        )

    # 2. Resolve Stage F2 candidate ranking asset
    ranking_asset: Asset | None = None
    if req_payload.candidate_ranking_id:
        try:
            r_candidate = default_asset_registry.get(req_payload.candidate_ranking_id)
            if r_candidate.investigation_id == investigation_id:
                ranking_asset = r_candidate
        except KeyError:
            pass

        if ranking_asset is None:
            for asset in default_asset_registry.list_for_investigation(investigation_id):
                extra = asset.provenance.extra or {}
                if (
                    asset.id == req_payload.candidate_ranking_id
                    or asset.provenance.product_id == req_payload.candidate_ranking_id
                    or extra.get("candidate_ranking_id") == req_payload.candidate_ranking_id
                    or asset.metadata.get("candidate_ranking_id") == req_payload.candidate_ranking_id
                    or (asset.type == AssetType.DOCUMENT and req_payload.candidate_ranking_id in str(asset.location))
                ):
                    ranking_asset = asset
                    break

        if ranking_asset is None:
            raise HTTPException(
                status_code=404,
                detail=f"Candidate ranking asset '{req_payload.candidate_ranking_id}' not found for investigation '{investigation_id}'",
            )
    else:
        # Auto-discover latest candidate ranking asset
        for asset in default_asset_registry.list_for_investigation(investigation_id):
            extra = asset.provenance.extra or {}
            if (
                asset.type == AssetType.DOCUMENT
                and (extra.get("asset_type") == "candidate_ranking" or asset.metadata.get("asset_type") == "candidate_ranking")
                and (extra.get("spill_detection_id") == spill_id or asset.metadata.get("spill_id") == spill_id)
            ):
                ranking_asset = asset
                break

        if ranking_asset is None:
            raise HTTPException(
                status_code=404,
                detail=f"Candidate ranking asset not found for investigation '{investigation_id}' and spill '{spill_id}'",
            )

    # 3. Load candidate ranking result
    try:
        candidate_ranking = load_candidate_ranking_from_asset(ranking_asset, default_asset_registry)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to load candidate ranking result: {exc}")

    # 4. Generate explainability report
    try:
        report, _ = generate_explainability_report(
            candidate_ranking=candidate_ranking,
            investigation_id=investigation_id,
            spill_id=spill_id,
        )
        return report
    except ExplainabilityError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Explainability report generation failed: {exc}")


# ===========================================================================
# STAGE G1 — Investigation API
# ===========================================================================

@router.post(
    "/api/v1/investigations",
    response_model=InvestigationResponse,
    status_code=201,
)
def create_investigation_endpoint(
    payload: InvestigationCreateRequest,
) -> InvestigationResponse:
    """Stage G1 — Create an investigation from user-supplied context."""
    inv = default_investigation_store.create(
        name=payload.name,
        area_of_interest=payload.area_of_interest,
        time_window=payload.time_window,
        description=payload.description,
        metadata=payload.metadata,
    )
    return InvestigationResponse(
        id=inv.id,
        name=inv.name,
        status=inv.status,
        area_of_interest=inv.area_of_interest,
        time_window=inv.time_window,
        created_at=inv.created_at,
        description=inv.description,
        metadata=inv.metadata,
        asset_ids=inv.asset_ids,
        evidence_ids=inv.evidence_ids,
    )


@router.get(
    "/api/v1/investigations",
    response_model=list[InvestigationListItem],
)
def list_investigations_endpoint() -> list[InvestigationListItem]:
    """Stage G1 — Return a deterministic list of investigations with basic metadata."""
    return default_investigation_store.list()


@router.get(
    "/api/v1/investigations/{investigation_id}",
    response_model=InvestigationResponse,
)
def get_investigation_endpoint(
    investigation_id: str,
) -> InvestigationResponse:
    """Stage G1 — Get structured investigation details."""
    inv = default_investigation_store.get(investigation_id)
    if inv is None:
        raise HTTPException(
            status_code=404,
            detail=f"Investigation '{investigation_id}' not found",
        )
    return InvestigationResponse(
        id=inv.id,
        name=inv.name,
        status=inv.status,
        area_of_interest=inv.area_of_interest,
        time_window=inv.time_window,
        created_at=inv.created_at,
        description=inv.description,
        metadata=inv.metadata,
        asset_ids=inv.asset_ids,
        evidence_ids=inv.evidence_ids,
    )


@router.get(
    "/api/v1/investigations/{investigation_id}/status",
    response_model=InvestigationStatusResponse,
)
def get_investigation_status_endpoint(
    investigation_id: str,
) -> InvestigationStatusResponse:
    """Stage G1 — Get structured processing state for an investigation."""
    st = default_investigation_store.get_status(investigation_id)
    if st is None:
        raise HTTPException(
            status_code=404,
            detail=f"Investigation '{investigation_id}' not found",
        )
    return st


@router.post(
    "/api/v1/investigations/{investigation_id}/run",
    response_model=InvestigationRunResponse,
)
def run_investigation_workflow_endpoint(
    investigation_id: str,
    payload: InvestigationRunRequest | None = None,
) -> InvestigationRunResponse:
    """Stage G1 — Orchestrate scientific pipeline stages B1 through F3 sequentially."""
    inv = default_investigation_store.get(investigation_id)
    if inv is None:
        raise HTTPException(
            status_code=404,
            detail=f"Investigation '{investigation_id}' not found",
        )
    if inv.status == InvestigationStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail="Investigation already completed. Create a new investigation to re-run the pipeline.",
        )
    req_payload = payload or InvestigationRunRequest()
    if req_payload.sentinel1_artifact_path:
        validate_safe_data_path(req_payload.sentinel1_artifact_path, settings.data_dir)
    try:
        return run_investigation_workflow(
            investigation_id=investigation_id,
            payload=req_payload,
            store=default_investigation_store,
            registry=default_asset_registry,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Workflow execution failed: {exc}")


@router.get(
    "/api/v1/investigations/{investigation_id}/artifacts",
    response_model=list[ArtifactSummary],
)
def list_investigation_artifacts_endpoint(
    investigation_id: str,
) -> list[ArtifactSummary]:
    """Stage G1 — List artifacts registered in AssetRegistry with provenance and metadata."""
    inv = default_investigation_store.get(investigation_id)
    if inv is None:
        raise HTTPException(
            status_code=404,
            detail=f"Investigation '{investigation_id}' not found",
        )
    return list_investigation_artifacts(investigation_id, registry=default_asset_registry)



