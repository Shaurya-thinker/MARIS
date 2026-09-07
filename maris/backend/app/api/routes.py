from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.models.satellite import SatelliteScene
from app.services.sentinel1_ingestion import Sentinel1IngestionError, ingest_sentinel1_artifact
from app.validation.schemas import ValidationResult

router = APIRouter()


class Sentinel1IngestRequest(BaseModel):
    artifact_path: str


class Sentinel1IngestResponse(BaseModel):
    scene: SatelliteScene
    validation: ValidationResult


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