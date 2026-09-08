from datetime import datetime
from enum import Enum
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


class CandidateGenerationStatus(str, Enum):
    """Status outcomes for Stage E1 candidate vessel generation."""

    COMPLETED = "completed"
    NO_CANDIDATES_FOUND = "no_candidates_found"
    AIS_DATA_UNAVAILABLE = "ais_data_unavailable"
    AIS_VALIDATION_FAILED = "ais_validation_failed"


class CandidateVessel(BaseModel):
    """A single vessel identified as spatially/temporally candidate for a D3 source zone.

    Zero-Fabrication Guarantee:
    - Never interpolated, reconstructed, or dead-reckoned.
    - Represents exclusively genuine, actual historical AIS observations.
    """

    candidate_id: str
    vessel_id: str
    mmsi: str | None = None
    imo: str | None = None
    vessel_name: str | None = None

    # Spatial proximity metrics
    inside_source_zone: bool = Field(
        description="True if at least one observed AIS position falls within the D3 source candidate zone"
    )
    min_distance_to_source_center_km: float = Field(
        ge=0.0,
        description="Great-circle distance from the closest observed AIS position to D3 source center point (km)"
    )
    distance_to_zone_boundary_km: float = Field(
        ge=0.0,
        description="Estimated distance to candidate zone boundary: 0.0 if inside, > 0.0 if outside"
    )

    # Closest Point of Approach (CPA) from actual observations
    closest_position_lon: float = Field(ge=-180.0, le=180.0)
    closest_position_lat: float = Field(ge=-90.0, le=90.0)
    closest_position_time: datetime
    time_offset_from_source_hours: float = Field(
        description="Signed time difference: (closest_position_time - source_time) in hours"
    )

    # Observed kinematics at closest point (if present in raw AIS observation)
    speed_over_ground: float | None = None
    course_over_ground: float | None = None
    heading: float | None = None
    navigation_status: str | None = None

    # Observation provenance
    observed_positions_count: int = Field(
        ge=1,
        description="Number of valid observed AIS positions for this vessel within the query window"
    )
    raw_positions: list[VesselPosition] = Field(
        default_factory=list,
        description="Actual observed historical AIS positions. Never interpolated or fabricated."
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateVesselGenerationResult(BaseModel):
    """Result of Stage E1 candidate vessel generation."""

    id: str
    investigation_id: str
    source_estimate_id: str
    spill_detection_id: str
    ais_asset_id: str | None = None
    derived_asset_id: str | None = None

    status: CandidateGenerationStatus
    source_time: datetime
    source_uncertainty_radius_km: float

    # Applied query envelope
    temporal_window_start: datetime
    temporal_window_end: datetime
    spatial_query_bbox: dict[str, float] = Field(
        description="Derived bounding box used for spatial query: {'west': w, 'south': s, 'east': e, 'north': n}"
    )

    candidate_count: int = Field(ge=0)
    total_vessels_checked: int = Field(ge=0)
    candidates: list[CandidateVessel] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

