from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.acquisition.registry import default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.core.config import settings
from app.models.asset import Asset
from app.models.common import AssetType, BBoxAreaOfInterest, BoundingBox, Provenance
from app.models.satellite import SatelliteScene, SpillDetection
from app.services.sentinel1_ingestion import Sentinel1IngestionError, ingest_sentinel1_artifact
from app.services.spill_detection import (
    AdaptiveThresholdSpillDetector,
    SpillDetectionError,
    detect_spills_from_sar_scene,
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


@router.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.service_name,
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
        path = Path(payload.sar_asset_path)
        if not path.exists():
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
            location=str(path.resolve()),
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