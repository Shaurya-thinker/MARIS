from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class VesselPosition(BaseModel):
    timestamp: datetime
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    speed: float | None = None
    heading: float | None = None
    course: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class VesselTrack(BaseModel):
    """Identity and positions for one vessel in an investigation, backed by a stored asset."""

    id: str
    investigation_id: str
    asset_id: str
    vessel_id: str
    mmsi: str | None = None
    imo: str | None = None
    name: str | None = None
    positions: list[VesselPosition] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
