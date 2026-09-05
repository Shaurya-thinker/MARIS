from datetime import datetime
from typing import Any

from pydantic import BaseModel


class SatelliteScene(BaseModel):
    id: str
    provider: str
    sensor: str
    acquisition_time: datetime
    geometry: dict[str, Any]
    resolution: float
    metadata: dict[str, Any]


class SpillDetection(BaseModel):
    detected: bool
    confidence: float
    geometry: dict[str, Any]
    area: float
    model_version: str