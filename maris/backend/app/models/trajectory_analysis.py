"""Domain models for MARIS Stage E2 — AIS Trajectory & Spatial/Temporal Analysis.

Stage E2 computes physical trajectory and spatio-temporal transit metrics
for candidate vessels produced by Stage E1 relative to the D3 source candidate zone
and backward drift trajectory.

ZERO-FABRICATION INVARIANT:
- Trajectory segments are strictly pairwise mathematical transitions between
  consecutive genuine observed AIS positions.
- No intermediate or synthetic positions are ever generated.
- In-zone transit duration is strictly (last_inside_time - first_inside_time)
  from actual observed points.
- Missing intervals/gaps are never treated as continuous observed transit.
- Centerline proximity and COG/drift angular differences are purely descriptive
  physical metrics; no behavioural or attribution scores are assigned.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TrajectorySegment(BaseModel):
    """Pairwise kinematic summary between two consecutive genuine observed AIS positions."""

    start_time: datetime = Field(description="Timestamp of the earlier observed AIS position (UTC)")
    end_time: datetime = Field(description="Timestamp of the later observed AIS position (UTC)")
    start_lon: float = Field(ge=-180.0, le=180.0, description="WGS84 longitude of start point")
    start_lat: float = Field(ge=-90.0, le=90.0, description="WGS84 latitude of start point")
    end_lon: float = Field(ge=-180.0, le=180.0, description="WGS84 longitude of end point")
    end_lat: float = Field(ge=-90.0, le=90.0, description="WGS84 latitude of end point")
    duration_seconds: float = Field(ge=0.0, description="Elapsed time between observations (seconds)")
    distance_m: float = Field(ge=0.0, description="Great-circle distance between observations (metres)")
    derived_speed_knots: float | None = Field(
        default=None,
        description="Average derived speed across segment: (distance_m / duration_s) in knots"
    )
    reported_sog_start: float | None = Field(
        default=None,
        description="Speed over ground reported by AIS at start point (knots)"
    )
    reported_sog_end: float | None = Field(
        default=None,
        description="Speed over ground reported by AIS at end point (knots)"
    )
    sog_difference_knots: float | None = Field(
        default=None,
        description="Difference between mean reported SOG and derived segment speed (knots)"
    )


class ZoneTransitProfile(BaseModel):
    """Quantitative spatio-temporal transit metrics inside the D3 source candidate zone."""

    points_inside_count: int = Field(
        ge=0,
        description="Count of actual observed AIS positions falling inside the D3 source candidate zone"
    )
    first_inside_time: datetime | None = Field(
        default=None,
        description="Timestamp of first actual observed ping inside the zone (UTC)"
    )
    last_inside_time: datetime | None = Field(
        default=None,
        description="Timestamp of last actual observed ping inside the zone (UTC)"
    )
    observed_transit_duration_seconds: float = Field(
        ge=0.0,
        default=0.0,
        description="Observed in-zone duration: (last_inside_time - first_inside_time) in seconds. Zero if < 2 inside pings."
    )
    min_distance_to_center_km: float = Field(
        ge=0.0,
        description="Minimum great-circle distance from observed pings to D3 source center point (km)"
    )
    distance_to_zone_boundary_km: float = Field(
        ge=0.0,
        description="Estimated distance to candidate zone boundary: 0.0 if inside, > 0.0 if outside (km)"
    )
    closest_position_time: datetime = Field(
        description="Timestamp of the actual observed ping closest to the D3 source center"
    )
    time_offset_from_source_hours: float = Field(
        description="Signed time difference: (closest_position_time - source_time) in hours"
    )
    cpa_lon: float = Field(ge=-180.0, le=180.0, description="Longitude of observed CPA position")
    cpa_lat: float = Field(ge=-90.0, le=90.0, description="Latitude of observed CPA position")


class CenterlineProximityProfile(BaseModel):
    """Spatial and directional proximity relative to the D3 backward drift trajectory centerline."""

    min_distance_to_centerline_km: float = Field(
        ge=0.0,
        description="Minimum great-circle distance from any observed candidate ping to the D3 backward drift centerline (km)"
    )
    closest_centerline_point_lon: float = Field(
        ge=-180.0, le=180.0,
        description="Longitude of the point on the D3 centerline closest to the vessel's track"
    )
    closest_centerline_point_lat: float = Field(
        ge=-90.0, le=90.0,
        description="Latitude of the point on the D3 centerline closest to the vessel's track"
    )
    course_drift_angle_diff_deg: float | None = Field(
        default=None,
        ge=0.0, le=180.0,
        description=(
            "Raw circular angular difference in [0, 180] deg between vessel COG and local reverse-drift direction. "
            "NOTE: This is descriptive physical data only, NOT a suspicion score or attribution metric."
        )
    )


class VesselTrajectoryAnalysis(BaseModel):
    """Physical trajectory and spatio-temporal transit analysis for a single candidate vessel."""

    candidate_id: str
    vessel_id: str
    mmsi: str | None = None
    imo: str | None = None
    vessel_name: str | None = None

    # Track-level kinematic statistics
    observed_positions_count: int = Field(ge=1)
    total_track_duration_seconds: float = Field(
        ge=0.0,
        description="Total elapsed time between first and last observed pings in the window (seconds)"
    )
    total_track_distance_m: float = Field(
        ge=0.0,
        description="Cumulative great-circle distance along observed sequential segments (metres)"
    )
    min_reported_sog_knots: float | None = None
    max_reported_sog_knots: float | None = None
    mean_reported_sog_knots: float | None = None
    mean_derived_speed_knots: float | None = None

    # Spatial and transit profiles
    transit_profile: ZoneTransitProfile
    centerline_proximity: CenterlineProximityProfile
    segments: list[TrajectorySegment] = Field(
        default_factory=list,
        description="Sequential pairwise observed segments. Zero if vessel has only 1 ping."
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class TrajectoryAnalysisResult(BaseModel):
    """Result of Stage E2 AIS Trajectory & Spatial/Temporal Analysis."""

    id: str
    investigation_id: str
    spill_detection_id: str
    source_estimate_id: str
    candidate_generation_id: str
    derived_asset_id: str | None = None

    analyzed_vessel_count: int = Field(ge=0)
    analyses: list[VesselTrajectoryAnalysis] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
