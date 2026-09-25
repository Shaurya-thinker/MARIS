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
    speed_mps: float | None = Field(
        default=None,
        description="Average derived speed across segment in metres per second (m/s)"
    )
    bearing_degrees: float | None = Field(
        default=None,
        ge=0.0, le=360.0,
        description="Initial forward bearing / azimuth from start to end point in degrees [0, 360)"
    )
    is_valid_kinematic: bool = Field(
        default=True,
        description="True if segment has duration > 0 and speed within physically plausible bounds"
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


class ObservedTrajectoryPoint(BaseModel):
    """Structured representation of a single genuine observed AIS observation."""

    timestamp: datetime = Field(description="Observation UTC timestamp")
    lon: float = Field(ge=-180.0, le=180.0, description="WGS84 longitude")
    lat: float = Field(ge=-90.0, le=90.0, description="WGS84 latitude")
    speed_over_ground: float | None = Field(default=None, description="AIS reported speed over ground (knots)")
    course_over_ground: float | None = Field(default=None, description="AIS reported course over ground (deg)")
    heading: float | None = Field(default=None, description="AIS reported true heading (deg)")
    inside_source_zone: bool = Field(default=False, description="True if position falls inside D3 candidate zone")
    distance_to_center_km: float = Field(ge=0.0, default=0.0, description="Distance to D3 source center point (km)")
    distance_to_polygon_km: float = Field(ge=0.0, default=0.0, description="Distance to D3 source polygon boundary (km, 0 if inside)")
    distance_to_centerline_km: float | None = Field(default=None, ge=0.0, description="Distance to D3 drift centerline (km)")
    seconds_from_source_time: float = Field(default=0.0, description="Signed time delta from D3 source_time (seconds)")
    seconds_from_spill_time: float | None = Field(default=None, description="Signed time delta from SAR spill detection time (seconds)")


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
    min_distance_to_polygon_km: float = Field(
        ge=0.0,
        default=0.0,
        description="Exact minimum distance from observed pings to D3 source polygon: 0.0 if inside, > 0.0 if outside (km)"
    )
    closest_position_time: datetime = Field(
        description="Timestamp of the actual observed ping closest to the D3 source center"
    )
    time_offset_from_source_hours: float = Field(
        description="Signed time difference: (closest_position_time - source_time) in hours"
    )
    cpa_lon: float = Field(ge=-180.0, le=180.0, description="Longitude of observed CPA position")
    cpa_lat: float = Field(ge=-90.0, le=90.0, description="Latitude of observed CPA position")
    cpa_to_polygon_distance_km: float = Field(
        ge=0.0,
        default=0.0,
        description="Distance at CPA to source polygon (km, 0.0 if any observation is inside)"
    )
    cpa_to_polygon_time: datetime | None = Field(
        default=None,
        description="Timestamp of the observed ping closest to the D3 source polygon"
    )
    cpa_to_polygon_lon: float | None = Field(
        default=None, ge=-180.0, le=180.0,
        description="Longitude of observed ping closest to the D3 source polygon"
    )
    cpa_to_polygon_lat: float | None = Field(
        default=None, ge=-90.0, le=90.0,
        description="Latitude of observed ping closest to the D3 source polygon"
    )


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
    closest_observation_time: datetime | None = Field(
        default=None,
        description="Timestamp of the candidate observation closest to the D3 centerline"
    )
    cpa_observation_lon: float | None = Field(
        default=None, ge=-180.0, le=180.0,
        description="Longitude of the candidate observation closest to the D3 centerline"
    )
    cpa_observation_lat: float | None = Field(
        default=None, ge=-90.0, le=90.0,
        description="Latitude of the candidate observation closest to the D3 centerline"
    )
    course_drift_angle_diff_deg: float | None = Field(
        default=None,
        ge=0.0, le=180.0,
        description=(
            "Raw circular angular difference in [0, 180] deg between vessel COG and local reverse-drift direction. "
            "NOTE: This is descriptive physical data only, NOT a suspicion score or attribution metric."
        )
    )


class TemporalCorrelationProfile(BaseModel):
    """Temporal correlation metrics relative to D3 source time and SAR detection time."""

    earliest_observation_time: datetime | None = Field(default=None, description="Timestamp of first observed ping")
    latest_observation_time: datetime | None = Field(default=None, description="Timestamp of last observed ping")
    temporal_span_seconds: float = Field(ge=0.0, default=0.0, description="Total duration spanned by observed trajectory (seconds)")
    closest_to_source_time: datetime | None = Field(default=None, description="Timestamp of observation closest in time to source_time")
    seconds_from_source_time: float = Field(default=0.0, description="Signed time delta (closest_to_source_time - source_time) in seconds")
    hours_from_source_time: float = Field(default=0.0, description="Signed time delta (closest_to_source_time - source_time) in hours")
    closest_to_spill_time: datetime | None = Field(default=None, description="Timestamp of observation closest in time to spill detection")
    seconds_from_spill_time: float | None = Field(default=None, description="Signed time delta (closest_to_spill_time - spill_time) in seconds")
    hours_from_spill_time: float | None = Field(default=None, description="Signed time delta (closest_to_spill_time - spill_time) in hours")


class TrajectoryQualityProfile(BaseModel):
    """Structured data quality and provenance indicators for the observed trajectory."""

    observation_count: int = Field(ge=0, description="Number of accepted genuine AIS observations after deduplication")
    duplicate_count: int = Field(ge=0, default=0, description="Number of exact duplicate AIS observations removed")
    temporal_span_seconds: float = Field(ge=0.0, default=0.0, description="Span of observed track (seconds)")
    has_multiple_observations: bool = Field(default=False, description="True if vessel has >= 2 observations")
    has_valid_kinematics: bool = Field(default=False, description="True if at least one segment has valid non-zero dt and plausible speed")
    invalid_kinematic_interval_count: int = Field(ge=0, default=0, description="Count of consecutive pairs with dt <= 0 or implausible speed")
    sparse_track: bool = Field(default=False, description="True if trajectory has <= 2 observations")
    is_real_observation: bool = Field(
        default=False,
        description="True ONLY if derived from authoritative live/operational AIS; False for benchmarks and reconstructions"
    )
    data_source_type: str = Field(
        default="curated_historical_reconstruction",
        description="Provenance category: 'curated_historical_reconstruction', 'synthetic_benchmark', 'unverified_import', or 'authoritative_raw_ais'"
    )
    quality_flags: list[str] = Field(
        default_factory=list,
        description="Descriptive quality flags (e.g. 'single_observation', 'sparse_track', 'duplicates_removed')"
    )


class VesselTrajectoryAnalysis(BaseModel):
    """Physical trajectory and spatio-temporal transit analysis for a single candidate vessel."""

    candidate_id: str
    vessel_id: str
    mmsi: str | None = None
    imo: str | None = None
    vessel_name: str | None = None
    vessel_type: str | None = None
    call_sign: str | None = None
    flag_country: str | None = None
    status: str = Field(
        default="completed",
        description="Analysis status: 'completed', 'single_observation', 'no_observations', or 'error'"
    )

    # Track-level kinematic statistics
    observed_positions_count: int = Field(ge=0)
    total_track_duration_seconds: float = Field(
        ge=0.0,
        default=0.0,
        description="Total elapsed time between first and last observed pings in the window (seconds)"
    )
    total_track_distance_m: float = Field(
        ge=0.0,
        default=0.0,
        description="Cumulative great-circle distance along observed sequential segments (metres)"
    )
    min_reported_sog_knots: float | None = None
    max_reported_sog_knots: float | None = None
    mean_reported_sog_knots: float | None = None
    mean_derived_speed_knots: float | None = None

    # Spatial and transit profiles
    transit_profile: ZoneTransitProfile
    centerline_proximity: CenterlineProximityProfile
    temporal_correlation: TemporalCorrelationProfile | None = None
    quality: TrajectoryQualityProfile | None = None
    observed_trajectory: list[ObservedTrajectoryPoint] = Field(default_factory=list)
    segments: list[TrajectorySegment] = Field(
        default_factory=list,
        description="Sequential pairwise observed segments. Zero if vessel has <= 1 ping."
    )
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="Grouped evidence categories (SPATIAL, TEMPORAL, KINEMATIC, DATA QUALITY) for F1 fusion handoff"
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
