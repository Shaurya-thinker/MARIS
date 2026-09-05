from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.common import AreaOfInterest


class SatelliteScene(BaseModel):
    """A satellite acquisition belonging to an investigation, backed by a stored asset."""

    id: str
    investigation_id: str
    asset_id: str
    provider: str
    sensor: str
    acquisition_time: datetime
    footprint: AreaOfInterest
    resolution: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SpillDetection(BaseModel):
    """Observed or derived spill geometry for an investigation. Not an inference runtime."""

    id: str
    investigation_id: str
    asset_id: str
    scene_id: str | None = None
    detected: bool
    confidence: float | None = Field(default=None, ge=0, le=1)
    geometry: dict[str, Any]
    area: float | None = None
    model_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
