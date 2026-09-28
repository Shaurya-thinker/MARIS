"""Domain models for MARIS Stage F1 — Multi-Source Spatio-Temporal Evidence Fusion.

Stage F1 fuses physical evidence from B3, D3, E1, E2, and contextual intelligence from
E3, producing a deterministic Composite Concordance Score per candidate vessel.

ARCHITECTURAL INVARIANTS:
1. PRIMARY CONCORDANCE SCORE — only 3 channels contribute:
   - Spatial proximity    (w=0.50 nominal)
   - Temporal proximity   (w=0.25 nominal)
   - Trajectory/centerline consistency (w=0.25 nominal)
   Dynamic re-normalisation over valid channels only; absent channels excluded from denominator.

2. BEHAVIOURAL INTELLIGENCE EXCLUSION — E3 findings are preserved as BehavioralContextSummary.
   They contribute exactly 0.00 to the composite concordance score.
   More anomalies NEVER imply higher physical concordance.

3. FORWARD DRIFT EXCLUSION — D1 forward drift is an optional auxiliary cross-check only.
   Supplying or omitting D1 must not change the primary score.

4. NON-RANKING — Candidates are preserved in strict E1 input order.
   No sorting, ranking, nomination, filtering, or elimination in F1.
   All ranking logic belongs to Stage F2.

5. ZERO FABRICATION — All calculations use only genuine AIS observations and existing
   upstream drift/trajectory products.

6. SCORE MEANING — The concordance score is a deterministic physical compatibility index
   in [0.0, 1.0].  It is NOT a probability, likelihood of guilt, responsibility score,
   or legal evidence.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SignalStatus(str, Enum):
    """Evaluation status of an individual evidence signal or primary channel.

    VALID            : Channel was evaluated with sufficient genuine data.
    INSUFFICIENT_DATA: Channel could not be evaluated due to inadequate upstream
                       data (e.g. vessel has only 1 AIS ping; COG not reported).
                       The channel is EXCLUDED from the weighted denominator.
    UNAVAILABLE      : Upstream product was not supplied for this run
                       (e.g. optional D1 forward drift not provided).
                       The channel is EXCLUDED from the weighted denominator.
    """

    VALID = "valid"
    INSUFFICIENT_DATA = "insufficient_data"
    UNAVAILABLE = "unavailable"


class EvidenceSignal(BaseModel):
    """Normalised evaluation of a single primary physical evidence channel.

    Contributes to the composite concordance score only when status == VALID.
    """

    channel_name: str = Field(
        description=(
            "Unique identifier of the primary evidence channel. "
            "One of: 'spatial_proximity', 'temporal_proximity', 'trajectory_consistency'."
        )
    )
    status: SignalStatus = SignalStatus.VALID
    score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Normalised channel score in [0.0, 1.0]. "
            "None when status != VALID. "
            "This is a dimensionless physical compatibility index, NOT a probability or guilt score."
        ),
    )
    nominal_weight: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Nominal weight allocated to this channel before dynamic re-normalisation. "
            "Spatial=0.50, Temporal=0.25, Trajectory=0.25."
        ),
    )
    raw_metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="Underlying physical metrics used to derive the score (provenance).",
    )
    rationale: str = Field(
        description=(
            "Deterministic mathematical description of how the score was derived, "
            "referencing the exact formula and input values."
        )
    )


class BehavioralContextSummary(BaseModel):
    """Contextual operational summary derived from Stage E3 Behavioural Intelligence.

    MANDATORY: This record contributes exactly 0.00 to the primary concordance score.
    It is preserved for maritime domain awareness and downstream Stage F3 explainability.
    More anomalies NEVER mean higher physical concordance with the spill source.

    Do NOT interpret any of these fields as evidence of guilt or responsibility.
    """

    total_anomalies_count: int = Field(
        default=0,
        ge=0,
        description="Total behavioural anomalies observed across all categories.",
    )
    anomalies_in_zone_count: int = Field(
        default=0,
        ge=0,
        description="Anomalies occurring inside the D3 source candidate zone.",
    )
    loitering_detected: bool = Field(
        default=False,
        description=(
            "True if an observed low-speed multi-course-change pattern was detected "
            "in genuine AIS pings. Not an inference of intent or discharge activity."
        ),
    )
    transmission_gaps_count: int = Field(
        default=0,
        ge=0,
        description="Count of observable AIS transmission gaps exceeding threshold.",
    )
    gaps_spanning_zone_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Gaps whose endpoints or chord span across or touch the D3 source zone. "
            "Factual proximity descriptor only. Does NOT infer vessel presence or activity."
        ),
    )
    speed_drop_in_zone: bool = Field(
        default=False,
        description="Observed abrupt speed reduction inside or near the candidate zone.",
    )
    speed_surge_near_zone: bool = Field(
        default=False,
        description="Observed abrupt speed increase near the candidate zone.",
    )
    nav_status_mismatch: bool = Field(
        default=False,
        description="Reported navigation status contradicts observed physical kinematics.",
    )
    anchor_swing_observed: bool = Field(
        default=False,
        description=(
            "Observed positional clustering consistent with anchored or stationary state. "
            "Positional envelope from genuine AIS pings only; not a precise anchor position."
        ),
    )
    notable_anomaly_types: list[str] = Field(
        default_factory=list,
        description="Anomaly type codes from E3 summary_flags (BehavioralAnomalyType values).",
    )


class ForwardDriftCrossCheck(BaseModel):
    """Auxiliary cross-check against D1 forward drift trajectory.

    MANDATORY: Excluded from the primary concordance score.
    D1 and D3 share the same ERA5/CMEMS metocean forcing and leeway parameters;
    weighting both would double-count environmental drift physics.

    evaluated=False means D1 was not supplied; the primary score is unaffected.
    """

    evaluated: bool = Field(
        default=False,
        description="True if D1 forward drift was provided and cross-checked.",
    )
    min_distance_to_forward_track_km: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Minimum great-circle distance from any candidate observed ping "
            "to the D1 forward slick advection track (km). None if not evaluated."
        ),
    )
    closest_forward_step_time: datetime | None = Field(
        default=None,
        description="Timestamp of the D1 step closest to the candidate track. None if not evaluated.",
    )
    cross_check_note: str = Field(
        default="",
        description="Short factual note describing the cross-check result.",
    )


class SpillEvidenceSummary(BaseModel):
    """B3 spill detection context and SAR dark-spot characterization."""

    spill_id: str | None = None
    detection_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Uncalibrated dark-spot detector confidence score in [0.0, 1.0]. "
            "CRITICAL: This is an uncalibrated feature score, NOT a probability of oil "
            "or probability of vessel responsibility."
        ),
    )
    spill_area_m2: float | None = None
    centroid_lon: float | None = None
    centroid_lat: float | None = None
    maritime_domain_status: str | None = None
    vv_vh_ratio: float | None = None
    detection_provenance: dict[str, Any] = Field(default_factory=dict)
    caveat: str = (
        "Uncalibrated dark-spot score from B3. "
        "Not a calibrated statistical probability of oil presence or vessel responsibility."
    )


class EnvironmentalEvidenceSummary(BaseModel):
    """C2 candidate-level metocean context."""

    wind_speed_ms: float | None = None
    wind_direction_from_deg: float | None = None
    wind_regime: str | None = None
    current_speed_ms: float | None = None
    current_direction_to_deg: float | None = None
    source_timestamp: str | None = None
    interpolation_method: str | None = None
    data_quality: str | None = None
    missing_data_status: str | None = None
    caveat: str = (
        "Contextual metocean conditions from C2. "
        "FAVORABLE_DETECTION_WINDOW is NOT proof that oil exists; it indicates "
        "radar backscatter physics permit capillary wave damping contrast."
    )


class SourceEvidenceSummary(BaseModel):
    """D3 backward drift source estimation evidence."""

    source_point_lon: float | None = None
    source_point_lat: float | None = None
    source_uncertainty_radius_km: float | None = None
    source_time: datetime | None = None
    steps_count: int = 0
    termination_status: str | None = None
    current_fallback_applied: bool = False
    shoreline_terminated: bool = False
    environmental_fallback_info: str | None = None


class AisEvidenceSummary(BaseModel):
    """E1 candidate generation identity and observation summary."""

    mmsi: str | None = None
    imo: str | None = None
    vessel_name: str | None = None
    vessel_type: str | None = None
    call_sign: str | None = None
    flag: str | None = None
    accepted_observation_count: int = 0
    spatial_filtering_passed: bool = True
    temporal_filtering_passed: bool = True
    data_source_type: str = "curated_historical_reconstruction"
    is_real_observation: bool = False


class SpatialEvidenceSummary(BaseModel):
    """E2 observed spatial distance and zone relationship metrics."""

    min_distance_to_center_km: float | None = None
    distance_to_zone_boundary_km: float | None = None
    inside_source_zone: bool = False
    min_distance_to_centerline_km: float | None = None
    cpa_lon: float | None = None
    cpa_lat: float | None = None
    cpa_time: datetime | None = None


class TemporalEvidenceSummary(BaseModel):
    """E2 observed temporal offset metrics."""

    time_offset_from_source_hours: float | None = None
    abs_time_offset_hours: float | None = None
    tau_scale_hours: float | None = None
    closest_observation_time: datetime | None = None
    temporal_span_hours: float | None = None


class KinematicEvidenceSummary(BaseModel):
    """E2 observed kinematic metrics."""

    reported_sog_knots: float | None = None
    derived_speed_mps: float | None = None
    bearing_degrees: float | None = None
    trajectory_continuity: str | None = None
    has_valid_kinematics: bool = False
    invalid_intervals_count: int = 0


class DataQualityEvidenceSummary(BaseModel):
    """E2 trajectory and AIS data quality indicators."""

    observed_positions_count: int = 0
    duplicate_count: int = 0
    sparse_track: bool = False
    quality_flags: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class CandidateEvidenceDetail(BaseModel):
    """Structured collection of raw/extracted evidence across all domains."""

    spill: SpillEvidenceSummary = Field(default_factory=SpillEvidenceSummary)
    environment: EnvironmentalEvidenceSummary = Field(default_factory=EnvironmentalEvidenceSummary)
    source: SourceEvidenceSummary = Field(default_factory=SourceEvidenceSummary)
    ais: AisEvidenceSummary = Field(default_factory=AisEvidenceSummary)
    spatial: SpatialEvidenceSummary = Field(default_factory=SpatialEvidenceSummary)
    temporal: TemporalEvidenceSummary = Field(default_factory=TemporalEvidenceSummary)
    kinematic: KinematicEvidenceSummary = Field(default_factory=KinematicEvidenceSummary)
    data_quality: DataQualityEvidenceSummary = Field(default_factory=DataQualityEvidenceSummary)


class NormalizedEvidenceProfile(BaseModel):
    """Dimensionless normalized representations in [0.0, 1.0]. Not probabilities."""

    spatial_proximity: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Dimensionless spatial compatibility in [0.0, 1.0]. Exp decay.",
    )
    temporal_proximity: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Dimensionless temporal compatibility in [0.0, 1.0]. Gaussian decay.",
    )
    trajectory_alignment: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Dimensionless trajectory consistency in [0.0, 1.0]. Centerline/angular alignment.",
    )
    source_zone_membership: bool = Field(
        default=False,
        description="Factual polygon containment boolean (inside D3 source zone).",
    )
    environmental_compatibility: str | None = Field(
        default=None,
        description="Categorical environmental compatibility regime from C2.",
    )
    trajectory_quality: str | None = Field(
        default=None,
        description="Categorical trajectory data quality status (e.g. SPARSE, ADEQUATE, NONE).",
    )


class EvidenceAvailabilityProfile(BaseModel):
    """Structured tracking of available, missing, and insufficient dimensions."""

    available_dimensions: list[str] = Field(
        default_factory=list,
        description="Evidence dimensions successfully evaluated with valid data.",
    )
    unavailable_dimensions: list[str] = Field(
        default_factory=list,
        description="Evidence dimensions that were unavailable or lacked sufficient data.",
    )
    missing_reasons: dict[str, str] = Field(
        default_factory=dict,
        description="Explanations for each missing or unavailable dimension.",
    )


class MultiSourceUncertainty(BaseModel):
    """Preserves uncertainties across domains without collapsing to a single fabricated number."""

    source_uncertainty_km: float = Field(
        default=0.0,
        ge=0.0,
        description="D3 source spatial uncertainty radius (km).",
    )
    source_fallback_applied: bool = Field(
        default=False,
        description="True if D3 employed fallback (e.g. wind-only drift, shoreline termination).",
    )
    trajectory_uncertainty: str = Field(
        default="unassessed",
        description="Assessment of AIS sampling density, observation gaps, or sparse track limitations.",
    )
    environmental_uncertainty: str = Field(
        default="unassessed",
        description="Assessment of ERA5/CMEMS resolution and metocean interpolation caveats.",
    )
    detection_uncertainty: str = Field(
        default="unassessed",
        description="Assessment of B3 dark-spot detection confidence and uncalibrated status.",
    )
    geospatial_uncertainty: str = Field(
        default="unassessed",
        description="Assessment of SAR geocoding / terrain distortion caveats.",
    )


class EvidenceProvenanceRecord(BaseModel):
    """Full data provenance tracking."""

    source_assets: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of upstream stage to asset/product ID.",
    )
    data_source_type: str = Field(
        default="curated_historical_reconstruction",
        description="Source classification of AIS observations.",
    )
    is_real_observation: bool = Field(
        default=False,
        description="False for benchmark/curated/synthetic AIS data; True only for raw live feeds.",
    )
    timestamps: dict[str, str] = Field(
        default_factory=dict,
        description="Relevant event and observation timestamps (ISO-8601).",
    )
    fallback_flags: dict[str, bool] = Field(
        default_factory=dict,
        description="Flags indicating upstream algorithmic or data fallback conditions.",
    )


class VesselFusedEvidence(BaseModel):
    """Fused multi-source evidence profile for a single candidate vessel.

    STRICTLY PRESERVES INPUT ORDER: input_index reflects the exact position in the
    E1 CandidateVesselGenerationResult.candidates list. F1 does not sort, rank,
    nominate, filter, or eliminate candidates.

    The composite_concordance_score is a physical compatibility index in [0.0, 1.0].
    It is NOT a probability, guilt score, responsibility measure, or legal evidence.
    """

    mmsi: str | None = Field(
        default=None,
        description="Vessel MMSI from E1 CandidateVessel.",
    )
    vessel_name: str | None = Field(
        default=None,
        description="Vessel name from E1 CandidateVessel.",
    )
    vessel_type: str | None = Field(
        default=None,
        description="Vessel type from E1 CandidateVessel metadata (if available).",
    )
    candidate_id: str = Field(
        description="E1 CandidateVessel.candidate_id.",
    )
    vessel_id: str = Field(
        description="E1 CandidateVessel.vessel_id.",
    )
    input_index: int = Field(
        ge=0,
        description=(
            "Zero-based index of this candidate in E1 CandidateVesselGenerationResult.candidates. "
            "Used to verify and enforce strict order preservation."
        ),
    )

    # -------- Primary Concordance Evaluation --------
    composite_concordance_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Weighted composite physical concordance index, dynamically normalised over "
            "available primary channels only. Range [0.0, 1.0]. "
            "None if no primary channels could be evaluated. "
            "CRITICAL: This is NOT a probability, guilt score, or attribution metric."
        ),
    )
    evidence_availability_ratio: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Ratio of primary evidence channels evaluated with valid data: "
            "N_valid_channels / 3. "
            "This is an F1 data availability metric, NOT an AIS transmission completeness measure."
        ),
    )
    primary_signals: list[EvidenceSignal] = Field(
        description=(
            "Ordered list of the 3 primary evidence channel evaluations: "
            "[spatial_proximity, temporal_proximity, trajectory_consistency]. "
            "Only signals with status=VALID contribute to the concordance score."
        )
    )

    # -------- Contextual / Supplemental Evidence (non-additive) --------
    behavioral_context: BehavioralContextSummary = Field(
        default_factory=BehavioralContextSummary,
        description=(
            "E3 behavioural intelligence context. "
            "Contributes exactly 0.00 to composite_concordance_score. "
            "Preserved for maritime domain awareness and Stage F3 explainability."
        ),
    )
    forward_drift_cross_check: ForwardDriftCrossCheck = Field(
        default_factory=ForwardDriftCrossCheck,
        description=(
            "Optional D1 forward drift cross-check. "
            "Excluded from composite_concordance_score to prevent double-counting "
            "of environmental drift physics already represented by D3."
        ),
    )

    # -------- Rich Structured Evidence & Provenance (F1 Evidence Blocks) --------
    evidence: CandidateEvidenceDetail = Field(
        default_factory=CandidateEvidenceDetail,
        description="Multi-source raw/extracted evidence across all domains (B3, C2, D3, E1, E2).",
    )
    normalized_evidence: NormalizedEvidenceProfile = Field(
        default_factory=NormalizedEvidenceProfile,
        description="Dimensionless normalized representations in [0.0, 1.0]. Not probabilities.",
    )
    availability: EvidenceAvailabilityProfile = Field(
        default_factory=EvidenceAvailabilityProfile,
        description="Detailed dimension availability and missing-data tracking.",
    )
    uncertainty: MultiSourceUncertainty = Field(
        default_factory=MultiSourceUncertainty,
        description="Multi-source uncertainty preservation across all analytical domains.",
    )
    provenance: EvidenceProvenanceRecord = Field(
        default_factory=EvidenceProvenanceRecord,
        description="Data provenance, asset tracing, and real/reconstructed observation classification.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal warnings, caveats, or limitations for this candidate.",
    )

    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceFusionRequest(BaseModel):
    """Request payload for Stage F1 evidence fusion."""

    source_estimate_id: str = Field(
        description="ID of the Stage D3 SourceEstimateResult or its registered asset."
    )
    candidate_generation_id: str = Field(
        description="ID of the Stage E1 CandidateVesselGenerationResult or its registered asset."
    )
    trajectory_analysis_id: str = Field(
        description="ID of the Stage E2 TrajectoryAnalysisResult or its registered asset."
    )
    behavioral_intelligence_id: str = Field(
        description="ID of the Stage E3 BehavioralIntelligenceResult or its registered asset."
    )
    forward_drift_id: str | None = Field(
        default=None,
        description="Optional ID of a Stage D1 DriftResult or its registered asset for cross-check.",
    )
    spill_detection_id: str | None = Field(
        default=None,
        description="Optional ID of the Stage B3 SpillDetection or registered asset.",
    )
    environmental_context_id: str | None = Field(
        default=None,
        description="Optional ID of the Stage C2 CandidateEnvironmentalContext or registered asset.",
    )


class EvidenceFusionResult(BaseModel):
    """Complete Stage F1 evidence fusion artifact.

    Links all upstream analytical products and records deterministic physical concordance
    evaluations for each candidate vessel in strict E1 input order.

    SCIENTIFIC LIMITATIONS:
    - composite_concordance_score is a heuristic physical compatibility index, NOT a
      calibrated statistical probability or Bayesian posterior.
    - High concordance indicates physical proximity to the probable slick origin at the
      estimated release time. It does NOT prove the vessel discharged oil.
    - Candidates with sparse AIS pings yield lower evidence_availability_ratio; F1 reports
      this explicitly rather than fabricating trajectory segments.
    - All ranking, nomination, and elimination of candidates is reserved for Stage F2.
    - Natural-language explanation, uncertainty auditing, and dossier synthesis belong to Stage F3.
    """

    id: str
    investigation_id: str
    spill_detection_id: str
    source_estimate_id: str
    candidate_generation_id: str
    trajectory_analysis_id: str
    behavioral_intelligence_id: str
    forward_drift_id: str | None = None
    asset_id: str | None = Field(
        default=None,
        description="AssetType.DOCUMENT asset registered in AssetRegistry for this fusion result.",
    )

    # Provenance and normalisation reference
    normalization_reference_radius_km: float = Field(
        gt=0.0,
        description=(
            "R_zone used for spatial normalisation, taken directly from "
            "source_estimate.source_uncertainty_radius_km. "
            "No synthetic radius is invented."
        ),
    )
    temporal_scale_hours: float = Field(
        gt=0.0,
        description=(
            "Temporal Gaussian decay scale τ_scale (hours) = max(2 * source_estimate.step_hours, 2.0). "
            "Derived from D3 integration parameters."
        ),
    )
    nominal_weights: dict[str, float] = Field(
        description=(
            "Nominal per-channel weights before dynamic re-normalisation. "
            "{'spatial_proximity': 0.50, 'temporal_proximity': 0.25, 'trajectory_consistency': 0.25}"
        )
    )

    candidate_count: int = Field(ge=0)
    fused_candidates: list[VesselFusedEvidence] = Field(
        description=(
            "Candidate fused evidence records in STRICT E1 input sequence. "
            "Unranked, unnominated, and uneliminated. Ordering belongs to Stage F2."
        )
    )

    spill_evidence_summary: SpillEvidenceSummary | None = Field(
        default=None,
        description="Investigation-level B3 SAR spill evidence summary if provided.",
    )
    environmental_evidence_summary: EnvironmentalEvidenceSummary | None = Field(
        default=None,
        description="Investigation-level C2 metocean evidence summary if provided.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Investigation-level evidence fusion warnings or caveats.",
    )

    metadata: dict[str, Any] = Field(default_factory=dict)

