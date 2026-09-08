"""Domain models for MARIS Stage E3 — Behavioural Intelligence.

Stage E3 provides deterministic behavioural and operational anomaly intelligence
for candidate vessels identified in Stage E1 and analyzed in Stage E2.

MANDATORY CONSTRAINTS:
1. AIS gaps must be reported only as observable transmission gaps. Never call them
   intentional "dark vessel" events and never infer transponder disabling or activity during the gap.
2. LOITERING_OBSERVED must mean only an observed low-speed, multi-course-change pattern
   in genuine AIS observations. Never infer intent, suspiciousness, discharge activity, or culpability.
3. Anchor-swing analysis must describe only the observed positional envelope/centroid from
   genuine AIS observations. Do not claim an exact physical anchor position or exact swing circle
   when sparse observations cannot support that precision.
4. ZERO-FABRICATION INVARIANT: Never interpolate, dead-reckon, reconstruct, or infer missing AIS positions.
5. EXCLUSION OF STAGE F: No attribution, evidence fusion, responsibility, or polluter ranking.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class BehavioralAnomalyType(str, Enum):
    """Categorization of observed operational and kinematic anomalies."""

    SPEED_DROP_IN_ZONE = "speed_drop_in_zone"
    SPEED_SURGE_NEAR_ZONE = "speed_surge_near_zone"
    SOG_DERIVED_SPEED_DISCREPANCY = "sog_derived_speed_discrepancy"
    COURSE_ALTERATION_IN_ZONE = "course_alteration_in_zone"
    LOITERING_OBSERVED = "loitering_observed"
    AIS_TRANSMISSION_GAP = "ais_transmission_gap"
    NAV_STATUS_MISMATCH = "nav_status_mismatch"
    ANCHOR_SWING_OBSERVED = "anchor_swing_observed"


class AnomalySeverity(str, Enum):
    """Significance level of the observed anomaly for maritime domain awareness."""

    INFO = "info"
    NOTABLE = "notable"
    ANOMALOUS = "anomalous"


class BehavioralAnomaly(BaseModel):
    """A single detected operational or kinematic anomaly based exclusively on genuine observations."""

    anomaly_type: BehavioralAnomalyType
    severity: AnomalySeverity = AnomalySeverity.NOTABLE
    description: str = Field(description="Descriptive explanation of the observed anomaly without intent inference")
    timestamp: datetime = Field(description="Timestamp of observation or anomaly start (UTC)")
    location_lon: float = Field(ge=-180.0, le=180.0, description="WGS84 longitude of anomaly observation")
    location_lat: float = Field(ge=-90.0, le=90.0, description="WGS84 latitude of anomaly observation")
    inside_source_zone: bool = Field(description="Whether anomaly occurred inside the D3 source candidate zone")
    observed_value: float | None = Field(default=None, description="Measured physical value (e.g. speed, angle)")
    baseline_or_threshold_value: float | None = Field(default=None, description="Reference threshold or baseline")
    details: dict[str, Any] = Field(default_factory=dict, description="Structured contextual metrics")


class TransmissionGap(BaseModel):
    """Observable transmission gap between consecutive genuine AIS observations.

    NOTE: Reports observable gap facts only. Does NOT infer intentional transponder deactivation,
    dark vessel activity, or vessel movements during the gap.
    """

    gap_start_time: datetime = Field(description="Timestamp of the last observation before gap (UTC)")
    gap_end_time: datetime = Field(description="Timestamp of the first observation after gap (UTC)")
    gap_duration_seconds: float = Field(ge=0.0, description="Elapsed duration of the transmission gap in seconds")
    gap_start_lon: float = Field(ge=-180.0, le=180.0, description="Longitude of pre-gap observation")
    gap_start_lat: float = Field(ge=-90.0, le=90.0, description="Latitude of pre-gap observation")
    gap_end_lon: float = Field(ge=-180.0, le=180.0, description="Longitude of post-gap observation")
    gap_end_lat: float = Field(ge=-90.0, le=90.0, description="Latitude of post-gap observation")
    distance_across_gap_km: float = Field(ge=0.0, description="Great-circle distance between gap endpoints (km)")
    spanned_source_zone: bool = Field(
        default=False,
        description="True if gap endpoints or straight-line chord span across or touch the D3 source candidate zone"
    )


class AnchorSwingProfile(BaseModel):
    """Observed positional envelope and centroid for an anchored or stationary vessel.

    NOTE: Describes only the observed positional envelope from genuine AIS pings.
    Does NOT claim an exact physical anchor drop point or chain length.
    """

    centroid_lon: float = Field(ge=-180.0, le=180.0, description="Mean longitude of observed anchored positions")
    centroid_lat: float = Field(ge=-90.0, le=90.0, description="Mean latitude of observed anchored positions")
    observed_envelope_radius_m: float = Field(
        ge=0.0,
        description="Maximum observed distance from centroid to any observed ping in the cluster (metres)"
    )
    observation_count: int = Field(ge=1, description="Number of observed positions in the anchored cluster")
    reported_nav_status: str | None = Field(default=None, description="Navigation status reported by AIS transponder")


class VesselBehavioralProfile(BaseModel):
    """Comprehensive behavioral and operational intelligence profile for a single candidate vessel."""

    candidate_id: str
    vessel_id: str
    mmsi: str | None = None
    imo: str | None = None
    vessel_name: str | None = None

    anomalies: list[BehavioralAnomaly] = Field(default_factory=list)
    transmission_gaps: list[TransmissionGap] = Field(default_factory=list)
    loitering_detected: bool = Field(
        default=False,
        description="True if an observed low-speed multi-course-change pattern was detected in genuine pings"
    )
    observed_loitering_duration_seconds: float = Field(
        ge=0.0,
        default=0.0,
        description="Duration of observed low-speed wandering cluster (seconds)"
    )
    nav_status_consistent: bool = Field(
        default=True,
        description="False if reported navigation status contradicts observed physical kinematics"
    )
    anchor_swing_profile: AnchorSwingProfile | None = Field(
        default=None,
        description="Observed positional envelope if vessel exhibited anchored/stationary state"
    )
    summary_flags: list[str] = Field(
        default_factory=list,
        description="Concise list of identified anomaly flag codes"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class BehavioralIntelligenceRequest(BaseModel):
    """Request payload for Stage E3 behavioural intelligence analysis."""

    source_estimate_id: str = Field(description="ID of the Stage D3 SourceEstimateResult or asset")
    candidate_generation_id: str = Field(description="ID of the Stage E1 CandidateVesselGenerationResult or asset")
    trajectory_analysis_id: str | None = Field(
        default=None,
        description="Optional ID of the Stage E2 TrajectoryAnalysisResult or asset"
    )
    speed_drop_threshold_knots: float = Field(
        default=5.0,
        ge=1.0,
        description="Speed reduction threshold (knots) to flag an abrupt deceleration"
    )
    loitering_speed_threshold_knots: float = Field(
        default=3.0,
        ge=0.1,
        description="Maximum speed (knots) characterizing observed low-speed wandering"
    )
    course_alteration_threshold_deg: float = Field(
        default=45.0,
        ge=10.0,
        le=180.0,
        description="Course alteration angle threshold (degrees) to flag significant turns"
    )
    transmission_gap_threshold_seconds: float = Field(
        default=1800.0,
        ge=60.0,
        description="Minimum duration (seconds) to record an observable AIS transmission gap (default 30 min)"
    )


class BehavioralIntelligenceResult(BaseModel):
    """Result of Stage E3 Behavioural Intelligence analysis."""

    id: str
    investigation_id: str
    spill_detection_id: str
    source_estimate_id: str
    candidate_generation_id: str
    trajectory_analysis_id: str | None = None
    derived_asset_id: str | None = None

    analyzed_vessel_count: int = Field(ge=0)
    profiles: list[VesselBehavioralProfile] = Field(default_factory=list)
    total_anomalies_detected: int = Field(ge=0)
    total_transmission_gaps_detected: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
