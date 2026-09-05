from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class InvestigationStatus(str, Enum):
    DRAFT = "draft"
    OPEN = "open"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class AssetType(str, Enum):
    SATELLITE_SCENE = "satellite_scene"
    IMAGERY_PREVIEW = "imagery_preview"
    SPILL_GEOMETRY = "spill_geometry"
    VESSEL_TRACK = "vessel_track"
    ENVIRONMENT_WIND = "environment_wind"
    ENVIRONMENT_CURRENT = "environment_current"
    DRIFT_PRODUCT = "drift_product"
    DOCUMENT = "document"
    OTHER = "other"


class EnvironmentKind(str, Enum):
    WIND = "wind"
    CURRENT = "current"
    OTHER = "other"


class EvidenceType(str, Enum):
    SPATIAL_PROXIMITY = "spatial_proximity"
    TEMPORAL_MATCH = "temporal_match"
    TRAJECTORY_CONSISTENCY = "trajectory_consistency"
    BEHAVIORAL = "behavioral"
    DOCUMENTARY = "documentary"
    OTHER = "other"


class BoundingBox(BaseModel):
    """Geographic bounding box in WGS84. Order: west, south, east, north."""

    west: float = Field(ge=-180, le=180)
    south: float = Field(ge=-90, le=90)
    east: float = Field(ge=-180, le=180)
    north: float = Field(ge=-90, le=90)

    @model_validator(mode="after")
    def south_before_north(self) -> "BoundingBox":
        if self.south >= self.north:
            raise ValueError("south must be less than north")
        return self


class TimeWindow(BaseModel):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def start_before_end(self) -> "TimeWindow":
        if self.start >= self.end:
            raise ValueError("time window start must be before end")
        return self


class BBoxAreaOfInterest(BaseModel):
    kind: Literal["bbox"] = "bbox"
    bbox: BoundingBox


class PolygonAreaOfInterest(BaseModel):
    """GeoJSON-style polygon: a list of linear rings of [lon, lat] positions."""

    kind: Literal["polygon"] = "polygon"
    coordinates: list[list[list[float]]]

    @field_validator("coordinates")
    @classmethod
    def require_closed_ring(cls, value: list[list[list[float]]]) -> list[list[list[float]]]:
        if not value or not value[0] or len(value[0]) < 4:
            raise ValueError("polygon requires at least one linear ring with four or more positions")
        exterior = value[0]
        if exterior[0] != exterior[-1]:
            raise ValueError("polygon exterior ring must be closed")
        for position in exterior:
            if len(position) < 2:
                raise ValueError("each polygon position must include longitude and latitude")
        return value


AreaOfInterest = Annotated[
    BBoxAreaOfInterest | PolygonAreaOfInterest,
    Field(discriminator="kind"),
]


class Provenance(BaseModel):
    """How an asset was obtained or derived. Provider-specific keys go in extra."""

    product_id: str | None = None
    license: str | None = None
    retrieved_at: datetime | None = None
    processing_level: str | None = None
    notes: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
