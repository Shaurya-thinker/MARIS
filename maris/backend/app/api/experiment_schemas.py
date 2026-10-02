"""Pydantic schemas for the real-data experiment API endpoints.

These models live exclusively in the API layer and are NOT imported by any
existing domain service.  They translate between HTTP request/response bodies
and the service layer DTOs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Sentinel-1 Discovery
# ---------------------------------------------------------------------------

class SentinelDiscoverRequest(BaseModel):
    west: float = Field(ge=-180.0, le=180.0, description="Bounding box west longitude (WGS84)")
    south: float = Field(ge=-90.0, le=90.0, description="Bounding box south latitude (WGS84)")
    east: float = Field(ge=-180.0, le=180.0, description="Bounding box east longitude (WGS84)")
    north: float = Field(ge=-90.0, le=90.0, description="Bounding box north latitude (WGS84)")
    start: datetime = Field(description="Search window start (UTC)")
    end: datetime = Field(description="Search window end (UTC)")
    limit: int = Field(default=20, ge=1, le=100, description="Maximum number of results")


class SentinelProductItem(BaseModel):
    product_id: str
    title: str
    sensing_start: str
    sensing_end: str | None
    online: bool
    platform: str
    mode: str | None
    product_class: str | None
    polarisation: str | None
    centroid_lon: float | None
    centroid_lat: float | None
    footprint: dict[str, Any] | None
    content_length_bytes: int | None


class SentinelDiscoverResponse(BaseModel):
    products: list[SentinelProductItem]
    count: int
    configured: bool


# ---------------------------------------------------------------------------
# Step 1b — Slick Detection & Characterization (Step 10)
# ---------------------------------------------------------------------------

class SlickCharacterizationItem(BaseModel):
    detected: bool = Field(description="Whether a candidate slick anomaly is confirmed in this scene")
    status: str = Field(description="Detection status: DETECTED (Verified Benchmark) | CATALOGUE_SELECTION | NO_SLICK_DETECTED")
    centroid_lon: float | None = Field(default=None, description="Observed slick centroid longitude (WGS84)")
    centroid_lat: float | None = Field(default=None, description="Observed slick centroid latitude (WGS84)")
    area_km2: float | None = Field(default=None, description="Estimated slick area in square kilometers (None if unavailable)")
    area_m2: float | None = Field(default=None, description="Estimated slick area in square meters (None if unavailable)")
    bbox: dict[str, float] | None = Field(default=None, description="Slick bounding box {west, south, east, north}")
    slick_geometry: dict[str, Any] | None = Field(default=None, description="GeoJSON Polygon/MultiPolygon of detected slick")
    observation_time: str = Field(description="Scene acquisition timestamp (ISO 8601 UTC)")
    satellite_product_id: str = Field(description="Sentinel-1 product identifier")
    platform: str = Field(default="Sentinel-1", description="Spacecraft platform")
    sensor: str = Field(default="Sentinel-1 C-SAR", description="Instrument type")
    mode: str | None = Field(default="IW GRDH", description="Acquisition mode")
    polarisation: str | None = Field(default="VV", description="Polarisation channel")
    confidence: float | None = Field(default=None, description="Quantitative detection confidence [0, 1] (None if unsegmented)")
    damping_contrast_db: float | None = Field(default=None, description="Radar Bragg damping contrast in dB (None if unsegmented)")
    estimated_age_hours: float | None = Field(default=None, description="Estimated slick age derived from backward drift backtrack duration")
    detection_method: str = Field(description="Algorithmic or metadata method used to locate/characterize the slick")
    provenance: str = Field(description="Data provenance / source organization")
    data_fidelity: str = Field(description="Honest statement of measurement fidelity")
    sar_raster_path: str | None = Field(default=None, description="Path to calibrated SAR raster if physically processed")
    has_physical_raster: bool = Field(default=False, description="Whether detection was performed on physical SAR raster pixels")
    pixel_count: int | None = Field(default=None, description="Number of detected slick pixels in raster")


class SentinelCharacterizeRequest(BaseModel):
    product_id: str
    title: str | None = None
    sensing_start: str | None = None
    centroid_lon: float | None = None
    centroid_lat: float | None = None
    mode: str | None = None
    polarisation: str | None = None
    footprint: dict[str, Any] | None = None
    backtrack_hours: float = 12.0
    sar_raster_path: str | None = None


class SentinelCharacterizeResponse(BaseModel):
    characterization: SlickCharacterizationItem


class SarAcquisitionRequest(BaseModel):
    product_id: str
    bbox: dict[str, float] | None = None
    centroid_lon: float | None = None
    centroid_lat: float | None = None
    sensing_time: str | None = None
    width_px: int = 800
    height_px: int = 800
    aoi_radius_km: float = 12.0
    force_refresh: bool = False


class SarAcquisitionResponse(BaseModel):
    success: bool
    sar_raster_path: str
    product_id: str
    file_size_bytes: int
    is_cached: bool
    source_provider: str
    data_authenticity: str
    is_test_fixture: bool
    crs: str
    bands: list[str]
    bbox: dict[str, float]
    message: str


# ---------------------------------------------------------------------------
# Step 11 — Forward Drift Prediction Schemas
# ---------------------------------------------------------------------------

class ForwardDriftStepItem(BaseModel):
    step: int
    timestamp: str
    lat: float
    lon: float
    u_wind_ms: float | None = None
    v_wind_ms: float | None = None
    u_current_ms: float | None = None
    v_current_ms: float | None = None
    drift_u_ms: float | None = None
    drift_v_ms: float | None = None
    cumulative_distance_km: float = 0.0
    forcing_mode: str = "current_plus_windage"
    current_fallback: bool = False
    current_source: str = "CMEMS"


class ForwardPredictionResultItem(BaseModel):
    model_version: str = "leeway_euler_v1"
    prediction_hours: float = 12.0
    step_hours: float = 1.0
    origin_lon: float
    origin_lat: float
    observation_lon: float | None = None
    observation_lat: float | None = None
    observation_time: str
    steps: list[ForwardDriftStepItem]
    final_lon: float
    final_lat: float
    total_distance_km: float
    displacement_km: float | None = None
    termination_status: str
    status: str | None = None
    forcing_modes: list[str] = Field(default_factory=lambda: ["current_plus_windage"])
    current_fallback_used: bool = False
    scientific_disclaimer: str
    provenance: str = "ECMWF ERA5 10m Wind + CMEMS GLORYS12V1 Surface Currents via Stage D1 Deterministic Leeway-Euler Engine"

    @model_validator(mode="before")
    @classmethod
    def _populate_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "observation_lon" not in data and "origin_lon" in data:
                data["observation_lon"] = data["origin_lon"]
            if "observation_lat" not in data and "origin_lat" in data:
                data["observation_lat"] = data["origin_lat"]
            if "origin_lon" not in data and "observation_lon" in data:
                data["origin_lon"] = data["observation_lon"]
            if "origin_lat" not in data and "observation_lat" in data:
                data["origin_lat"] = data["observation_lat"]
            if "displacement_km" not in data and "total_distance_km" in data:
                data["displacement_km"] = data["total_distance_km"]
            if "total_distance_km" not in data and "displacement_km" in data:
                data["total_distance_km"] = data["displacement_km"]
            if "status" not in data and "termination_status" in data:
                data["status"] = data["termination_status"]
            if "termination_status" not in data and "status" in data:
                data["termination_status"] = data["status"]
            if "prediction_hours" not in data:
                data["prediction_hours"] = float(len(data.get("steps", []))) or 12.0
            if "step_hours" not in data:
                data["step_hours"] = 1.0
        return data


class ForwardPredictionRequest(BaseModel):
    satellite_product_id: str = ""
    observation_time: str
    origin_lon: float | None = None
    origin_lat: float | None = None
    observation_lon: float | None = None
    observation_lat: float | None = None
    era5_netcdf_path: str
    cmems_netcdf_path: str
    prediction_hours: float = 12.0
    step_hours: float = 1.0
    leeway_fraction: float = 0.035
    slick_characterization: SlickCharacterizationItem | None = None

    @model_validator(mode="before")
    @classmethod
    def _align_coords(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if data.get("origin_lon") is None and data.get("observation_lon") is not None:
                data["origin_lon"] = data["observation_lon"]
            if data.get("origin_lat") is None and data.get("observation_lat") is not None:
                data["origin_lat"] = data["observation_lat"]
            if data.get("observation_lon") is None and data.get("origin_lon") is not None:
                data["observation_lon"] = data["origin_lon"]
            if data.get("observation_lat") is None and data.get("origin_lat") is not None:
                data["observation_lat"] = data["origin_lat"]
        return data


class ForwardPredictionResponse(BaseModel):
    prediction: ForwardPredictionResultItem
    forward_prediction: ForwardPredictionResultItem | None = None

    @model_validator(mode="before")
    @classmethod
    def _align_prediction(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "prediction" in data and "forward_prediction" not in data:
                data["forward_prediction"] = data["prediction"]
            elif "forward_prediction" in data and "prediction" not in data:
                data["prediction"] = data["forward_prediction"]
        return data


# ---------------------------------------------------------------------------
# Environment Selection
# ---------------------------------------------------------------------------

class EnvironmentSelectRequest(BaseModel):
    observation_time: datetime = Field(description="Sentinel-1 scene acquisition timestamp (UTC)")
    west: float = Field(ge=-180.0, le=180.0)
    south: float = Field(ge=-90.0, le=90.0)
    east: float = Field(ge=-180.0, le=180.0)
    north: float = Field(ge=-90.0, le=90.0)
    backtrack_hours: float = Field(default=12.0, ge=1.0, le=72.0)
    investigation_id: str = Field(default="real-experiment")
    era5_override_path: str | None = Field(default=None, description="Use existing ERA5 NetCDF instead of acquiring")
    cmems_override_path: str | None = Field(default=None, description="Use existing CMEMS NetCDF instead of acquiring")


class EnvironmentSelectResponse(BaseModel):
    era5_netcdf_path: str
    era5_timestamp: str
    era5_u_sample: float | None
    era5_v_sample: float | None
    cmems_netcdf_path: str
    cmems_timestamp: str
    cmems_u_sample: float | None
    cmems_v_sample: float | None
    auto_selected: bool
    era5_configured: bool
    cmems_configured: bool


# ---------------------------------------------------------------------------
# AIS Search
# ---------------------------------------------------------------------------

class AisSearchRequest(BaseModel):
    west: float = Field(default=0.0, ge=-180.0, le=180.0)
    south: float = Field(default=0.0, ge=-90.0, le=90.0)
    east: float = Field(default=0.0, ge=-180.0, le=180.0)
    north: float = Field(default=0.0, ge=-90.0, le=90.0)
    start: datetime | None = None
    end: datetime | None = None
    investigation_id: str = Field(default="real-experiment")
    backward_steps: list[dict[str, Any]] | None = None
    observation_time: datetime | None = None
    backtrack_hours: float | None = None
    source_candidate_zone: dict[str, Any] | None = None
    observation_lat: float | None = None
    observation_lon: float | None = None
    era5_netcdf_path: str | None = None
    cmems_netcdf_path: str | None = None
    step_hours: float | None = None
    spill_area_m2: float | None = None
    satellite_product_id: str | None = None


class VesselSummaryItem(BaseModel):
    mmsi: str | None
    vessel_name: str | None
    imo: str | None = None
    position_count: int
    first_timestamp: str
    last_timestamp: str
    source_adapter: str
    positions: list[dict[str, Any]] = Field(default_factory=list)
    min_trajectory_distance_km: float | None = None
    trajectory_time_delta_hours: float | None = None
    source_zone_intersection: bool = False
    min_source_distance_km: float | None = None
    source_type: str | None = "sqlite_ais"
    provider_name: str | None = "ais_vessels.db"


class AisSearchResponse(BaseModel):
    vessels: list[VesselSummaryItem]
    total_positions: int
    search_bbox: dict[str, float]
    search_window_start: str
    search_window_end: str
    adapter_id: str
    configured: bool
    backward_steps: list[dict[str, Any]] | None = None


class AisPositionsRequest(BaseModel):
    mmsis: list[str] = Field(description="MMSIs or vessel IDs to fetch positions for")
    west: float | None = Field(default=None, ge=-180.0, le=180.0)
    south: float | None = Field(default=None, ge=-90.0, le=90.0)
    east: float | None = Field(default=None, ge=-180.0, le=180.0)
    north: float | None = Field(default=None, ge=-90.0, le=90.0)
    start: datetime
    end: datetime


class AisPositionsResponse(BaseModel):
    vessel_positions: dict[str, list[dict[str, Any]]]
    total_positions: int


# ---------------------------------------------------------------------------
# Step 12 — ML Attribution + AIS Behavioral Intelligence Schemas
# ---------------------------------------------------------------------------

class BehavioralAnomalyItemSchema(BaseModel):
    """One detected behavioral anomaly for a candidate vessel."""
    anomaly_type: str
    severity: str
    description: str
    timestamp: str
    location_lon: float
    location_lat: float
    inside_source_zone: bool
    observed_value: float | None = None
    baseline_or_threshold_value: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class VesselBehavioralIntelligenceItem(BaseModel):
    """Behavioral intelligence summary for one candidate vessel (contextual only)."""
    anomalies: list[BehavioralAnomalyItemSchema] = Field(default_factory=list)
    transmission_gap_count: int = 0
    loitering_detected: bool = False
    observed_loitering_duration_seconds: float = 0.0
    nav_status_consistent: bool = True
    summary_flags: list[str] = Field(default_factory=list)
    analysis_note: str = (
        "Deterministic rule-based behavioral analysis (Stage E3 detectors). "
        "Findings are contextual only and do not alter physical drift attribution scores."
    )


# ---------------------------------------------------------------------------
# Experiment Run
# ---------------------------------------------------------------------------

class VesselInput(BaseModel):
    """One vessel to include in the attribution experiment."""
    mmsi: str | None = None
    vessel_name: str | None = None
    id: str | None = None
    positions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="AIS positions: [{timestamp, lat, lon, speed?, heading?}]"
    )


class MonteCarloConfigRequest(BaseModel):
    """Configuration for Phase #5 Monte Carlo Ensemble Uncertainty Propagation."""
    enabled: bool = Field(default=False, description="Enable Monte Carlo ensemble uncertainty propagation")
    ensemble_size: int = Field(default=50, ge=10, le=500, description="Number of Monte Carlo realizations")
    seed: int | None = Field(default=None, description="Optional random seed for reproducibility")
    perturb_origin: bool = Field(default=True, description="Perturb initial spill origin position")
    origin_std_m: float = Field(default=1000.0, ge=0.0, description="Initial origin position standard deviation in meters")
    perturb_leeway: bool = Field(default=True, description="Perturb wind leeway fraction")
    leeway_std: float = Field(default=0.005, ge=0.0, description="Leeway standard deviation around baseline 0.035")
    perturb_wind: bool = Field(default=True, description="Perturb wind velocity and direction")
    wind_speed_std_ms: float = Field(default=1.0, ge=0.0, description="Wind speed standard deviation in m/s")
    wind_dir_std_deg: float = Field(default=10.0, ge=0.0, description="Wind direction standard deviation in degrees")
    perturb_current: bool = Field(default=True, description="Perturb near-surface current velocity")
    current_std_ms: float = Field(default=0.05, ge=0.0, description="Current velocity standard deviation in m/s")


class ExperimentRunRequest(BaseModel):
    satellite_product_id: str = Field(description="CDSE Sentinel-1 product ID")
    observation_lon: float = Field(ge=-180.0, le=180.0)
    observation_lat: float = Field(ge=-90.0, le=90.0)
    observation_time: datetime
    era5_netcdf_path: str = Field(description="Local path to ERA5 NetCDF acquired in Step 2")
    cmems_netcdf_path: str = Field(description="Local path to CMEMS NetCDF acquired in Step 2")
    backtrack_hours: float = Field(default=12.0, ge=1.0, le=72.0)
    step_hours: float = Field(default=1.0, ge=0.25, le=6.0)
    spill_area_m2: float | None = Field(default=None, description="Observed slick area (m²)")
    selected_vessels: list[VesselInput] = Field(
        default_factory=list,
        description="Vessels to score against the reconstructed source zone"
    )
    search_bbox: dict[str, float] | None = Field(
        default=None,
        description="Optional spatial search bounding box {west, south, east, north}"
    )
    slick_characterization: SlickCharacterizationItem | None = Field(
        default=None,
        description="Optional Step 10 automated slick characterization data"
    )
    forward_prediction_hours: float | None = Field(
        default=None,
        description="Optional Step 11 forward prediction duration in hours"
    )
    forward_step_hours: float = Field(
        default=1.0,
        description="Step 11 forward integration step size in hours"
    )
    monte_carlo: MonteCarloConfigRequest | None = Field(
        default=None,
        description="Optional Step 5 Monte Carlo Ensemble configuration"
    )


# ---------------------------------------------------------------------------
# Phase #4 — Attribution & Explainability Schemas
# ---------------------------------------------------------------------------

class EvidenceComponent(BaseModel):
    score: float = Field(description="Normalized component score in [0, 1]")
    weight: float = Field(description="Weight of this component in the final score")


class EvidenceBreakdown(BaseModel):
    spatial: EvidenceComponent = Field(description="Spatial proximity component")
    temporal: EvidenceComponent = Field(description="Temporal overlap component")
    trajectory: EvidenceComponent = Field(description="Trajectory and kinematic alignment component")


class VesselFeaturesItem(BaseModel):
    vessel_id: str
    vessel_name: str | None
    mmsi: str | None
    min_source_distance_km: float | None
    temporal_overlap_hours: float
    trajectory_overlap_fraction: float
    heading_consistency: float | None
    speed_consistency: float | None
    ais_position_count: int
    ais_coverage_fraction: float
    evidence_consistency_score: float
    rank: int
    has_meaningful_support: bool
    positions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Authentic AIS positions for this candidate: [{timestamp, lat, lon, speed?, heading?}]"
    )
    min_trajectory_distance_km: float | None = None
    trajectory_time_delta_hours: float | None = None
    source_zone_intersection: bool = False
    source_type: str | None = "sqlite_ais"
    provider_name: str | None = "ais_vessels.db"
    # Step 12 — ML Model Probability
    model_probability: float | None = Field(
        default=None,
        description="Independent ML Model Probability in [0, 1]. Not a probability of legal responsibility or causation. Training provenance: Model trained on synthetic benchmark scenarios; real-data inference is an experimental contextual signal and has not been established as a calibrated real-world responsibility probability. NOT forced to sum to 1 across candidates."
    )
    ml_feature_vector: dict[str, float] | None = Field(
        default=None,
        description="ML feature-vector values are model inputs produced by the feature extractor and may use definitions or normalization different from the physical evidence presentation metrics."
    )
    # Step 12 — AIS Behavioral Intelligence
    behavioral_intelligence: VesselBehavioralIntelligenceItem | None = Field(
        default=None,
        description="Contextual rule-based detector findings. Does NOT modify evidence_consistency_score."
    )
    # Phase #4 — Attribution & Explainability
    evidence_breakdown: EvidenceBreakdown | None = None
    explanation: list[str] = Field(
        default_factory=list,
        description="Factual statements explaining spatiotemporal evidence consistency"
    )
    consistency_level: Literal["HIGH", "MODERATE", "LOW"] | str = Field(
        default="LOW",
        description="Consistency classification (HIGH >= 0.75, MODERATE >= 0.50, LOW < 0.50)"
    )
    scientific_disclaimer: str = Field(
        default="Evidence consistency indicates spatiotemporal correlation with the reconstructed drift model, not legal liability.",
        description="Neutral scientific disclaimer"
    )
    # Phase #5 — Monte Carlo Ensemble Evidence & Sensitivity
    ensemble_evidence: EnsembleEvidenceItem | None = None


# ---------------------------------------------------------------------------
# Phase #5 — Monte Carlo Ensemble Result Schemas
# ---------------------------------------------------------------------------

class EnsembleTrajectoryStepItem(BaseModel):
    step: int
    lat: float
    lon: float
    timestamp: str | None = None
    uncertainty_radius_m: float | None = None
    drift_u_ms: float | None = None
    drift_v_ms: float | None = None


class EnsembleRealizationItem(BaseModel):
    realization_id: int
    steps: list[EnsembleTrajectoryStepItem]
    final_source_lon: float
    final_source_lat: float
    final_uncertainty_radius_m: float
    perturbation_parameters: dict[str, float]
    termination_status: str = "completed"
    provenance: str = "MODEL_GENERATED_MONTE_CARLO_REALIZATION"


class EnsembleEvidenceItem(BaseModel):
    ensemble_support_fraction: float = Field(description="Fraction of realizations with spatiotemporal overlap")
    trajectory_consistency_across_ensemble: float | None = None
    score_mean: float | None = None
    score_std: float | None = None
    score_p05: float | None = None
    score_p95: float | None = None
    ensemble_disclaimer: str = Field(
        default="Ensemble support represents the fraction of configured model realizations exhibiting spatiotemporal correlation under perturbed forcing. It is a sensitivity measure, not a probability of causation or legal liability."
    )


class MonteCarloEnsembleResultItem(BaseModel):
    ensemble_size: int
    realizations: list[EnsembleRealizationItem]
    mean_trajectory: list[dict[str, Any]]
    final_source_centroid: dict[str, float]
    dispersion_radius_km: float
    p05_source_lon: float
    p95_source_lon: float
    p05_source_lat: float
    p95_source_lat: float
    effective_seed: int | None = None
    execution_time_ms: float = 0.0
    provenance: str = "MODEL_GENERATED_MONTE_CARLO_REALIZATION"
    scientific_disclaimer: str = Field(
        default="Monte Carlo ensemble trajectories represent model-generated uncertainty realizations under configured physical parameter perturbations. They are NOT observed vessel tracks or satellite observations, and ensemble frequencies do NOT constitute a probability of legal responsibility."
    )


class ExperimentRunResponse(BaseModel):
    run_id: str
    satellite_product_id: str
    observation_time: str
    backtrack_hours: float
    step_hours: float
    model_version: str
    source_lon: float
    source_lat: float
    source_radius_m: float
    source_zone_geojson: dict[str, Any]
    backward_steps: list[dict[str, Any]]
    vessels: list[VesselFeaturesItem]
    era5_path: str
    cmems_path: str
    created_at: str
    scientific_disclaimer: str
    observation_lon: float | None = None
    observation_lat: float | None = None
    observation_source: str = "BENCHMARK_FALLBACK"
    status: str = "completed"
    slick_characterization: SlickCharacterizationItem | None = None
    forward_prediction: ForwardPredictionResultItem | None = None
    monte_carlo_ensemble: MonteCarloEnsembleResultItem | None = None


class ExperimentRunSummary(BaseModel):
    run_id: str
    satellite_product_id: str
    observation_time: str
    backtrack_hours: float
    model_version: str
    source_lon: float
    source_lat: float
    source_radius_m: float
    created_at: str
    observation_lon: float | None = None
    observation_lat: float | None = None
    observation_source: str = "BENCHMARK_FALLBACK"
    candidate_count: int = 0
    status: str = "completed"
    slick_characterization: SlickCharacterizationItem | None = None
    forward_prediction: ForwardPredictionResultItem | None = None


class ExperimentListResponse(BaseModel):
    runs: list[ExperimentRunSummary]
    count: int


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class ExperimentConfigResponse(BaseModel):
    sentinel1_configured: bool
    era5_configured: bool
    cmems_configured: bool
    ais_configured: bool
    ais_adapter_id: str
    all_configured: bool
    warnings: list[str]


# ---------------------------------------------------------------------------
# Synthetic Scenario & ML Attribution Schemas
# ---------------------------------------------------------------------------

class SyntheticGenerateRequest(BaseModel):
    seed: int | None = None
    origin_lat: float | None = None
    origin_lon: float | None = None
    observation_time: datetime | None = None
    wind_speed_ms: float | None = None
    wind_direction_deg: float | None = None
    current_speed_ms: float | None = None
    current_direction_deg: float | None = None
    candidate_count: int = Field(default=4, ge=2, le=10)
    backtrack_hours: float = Field(default=6.0, ge=1.0, le=48.0)
    step_hours: float = Field(default=0.5, ge=0.1, le=2.0)
    spill_area_m2: float | None = None


class SyntheticGenerateResponse(BaseModel):
    scenario_id: str
    seed: int
    observation_time: str
    backtrack_hours: float
    step_hours: float
    origin_lon: float
    origin_lat: float
    spill_area_m2: float
    wind_speed_ms: float
    wind_direction_deg: float
    current_speed_ms: float
    current_direction_deg: float
    ground_truth_vessel_id: str
    estimated_source_lon: float
    estimated_source_lat: float
    candidate_count: int
    vessels: list[dict[str, Any]]
    is_synthetic: bool = True


class SyntheticRunRequest(BaseModel):
    scenario_id: str | None = None
    seed: int | None = None
    origin_lat: float | None = None
    origin_lon: float | None = None
    observation_time: datetime | None = None
    wind_speed_ms: float | None = None
    wind_direction_deg: float | None = None
    current_speed_ms: float | None = None
    current_direction_deg: float | None = None
    candidate_count: int = Field(default=4, ge=2, le=10)
    backtrack_hours: float = Field(default=6.0, ge=1.0, le=48.0)
    step_hours: float = Field(default=0.5, ge=0.1, le=2.0)
    spill_area_m2: float | None = None
    model_id: str | None = None


class SyntheticCandidateScored(BaseModel):
    vessel_id: str
    vessel_name: str
    mmsi: str | None
    vessel_type: str
    rank: int
    model_probability: float
    scenario_normalized_attribution_score: float
    has_meaningful_support: bool
    features: dict[str, float]
    positions: list[dict[str, Any]]
    is_ground_truth: bool


class SyntheticRunResponse(BaseModel):
    run_id: str
    is_synthetic: bool = True
    scenario_id: str
    seed: int
    observation_time: str
    backtrack_hours: float
    step_hours: float
    origin_lon: float
    origin_lat: float
    spill_area_m2: float
    wind_speed_ms: float
    wind_direction_deg: float
    current_speed_ms: float
    current_direction_deg: float
    u10: float
    v10: float
    uo: float
    vo: float
    model_id: str
    model_type: str
    source_lon: float
    source_lat: float
    source_radius_m: float
    source_zone_geojson: dict[str, Any]
    backward_steps: list[dict[str, Any]]
    vessels: list[SyntheticCandidateScored]
    ground_truth_vessel_id: str
    top_candidate_id: str | None
    attribution_match: bool
    model_coefficients: dict[str, float]
    model_test_metrics: dict[str, Any]


class ModelTrainRequest(BaseModel):
    num_scenarios: int = Field(default=35, ge=10, le=100)
    base_seed: int = Field(default=42)
    model_type: str = Field(default="logistic_regression")
    c_regularization: float = Field(default=1.0, ge=0.01, le=100.0)


class ModelTrainResponse(BaseModel):
    model_id: str
    model_type: str
    val_metrics: dict[str, Any]
    test_metrics: dict[str, Any]
    feature_coefficients: dict[str, float]
    training_sample_count: int
    training_scenario_count: int
    trained_at: str


class ActiveModelResponse(BaseModel):
    model_id: str
    model_type: str
    trained_at: str
    feature_coefficients: dict[str, float]
    val_metrics: dict[str, Any]
    test_metrics: dict[str, Any]
    training_sample_count: int
    training_scenario_count: int


# ---------------------------------------------------------------------------
# Evaluator Investigation Workflow Schemas
# ---------------------------------------------------------------------------

class EvaluatorReferenceObservationItem(BaseModel):
    id: str
    title: str
    image_file: str
    image_path: str
    region: str
    observation_lon: float
    observation_lat: float
    observation_time: str
    sensor: str
    mode: str
    slick_area_km2: float
    is_historical_demo: bool
    historical_context: str
    default_wind_speed_ms: float
    default_wind_direction_deg: float
    default_current_speed_ms: float
    default_current_direction_deg: float
    backtrack_hours: float
    benchmark_candidates: list[dict[str, Any]] = Field(default_factory=list)
    sar_subscene_path: str | None = None
    has_physical_raster: bool = False
    raster_provenance: str | None = None
    detected_slick_metrics: dict[str, Any] | None = None


class EvaluatorDriftPreviewRequest(BaseModel):
    origin_lon: float = Field(ge=-180.0, le=180.0)
    origin_lat: float = Field(ge=-90.0, le=90.0)
    observation_time: datetime
    wind_speed_ms: float = Field(ge=0.0, le=50.0)
    wind_direction_deg: float = Field(ge=0.0, le=360.0)
    current_speed_ms: float = Field(ge=0.0, le=5.0)
    current_direction_deg: float = Field(ge=0.0, le=360.0)
    backtrack_hours: float = Field(default=6.0, ge=0.5, le=72.0)
    step_hours: float = Field(default=0.5, ge=0.1, le=4.0)
    spill_area_m2: float = Field(default=100000.0, ge=1000.0)
    selected_image_id: str | None = None
    observation_source: str | None = None


class EvaluatorDriftPreviewResponse(BaseModel):
    observation_point: dict[str, float]
    observation_time: str
    observation_source: str = "BENCHMARK_FALLBACK"
    backtrack_hours: float
    step_hours: float
    reconstructed_source: dict[str, Any]
    backward_steps: list[dict[str, Any]]
    wind_vector: dict[str, float]
    current_vector: dict[str, float]


class EvaluatorFilterVesselsRequest(BaseModel):
    vessels: list[dict[str, Any]]
    backward_steps: list[dict[str, Any]]
    source_lon: float
    source_lat: float
    source_radius_m: float
    observation_time: datetime
    backtrack_hours: float = Field(default=6.0, ge=0.5, le=72.0)
    corridor_km: float = Field(default=25.0, ge=1.0, le=200.0)


class EvaluatorFilterVesselsResponse(BaseModel):
    corridor_km: float
    temporal_window: dict[str, str]
    total_evaluated: int
    eligible_count: int
    ineligible_count: int
    eligible_candidates: list[dict[str, Any]]
    ineligible_candidates: list[dict[str, Any]]
    provider_status: str
    provider_description: str


class EvaluatorRunRequest(BaseModel):
    selected_image_id: str
    observation_lon: float = Field(ge=-180.0, le=180.0)
    observation_lat: float = Field(ge=-90.0, le=90.0)
    observation_time: datetime
    wind_speed_ms: float = Field(ge=0.0, le=50.0)
    wind_direction_deg: float = Field(ge=0.0, le=360.0)
    current_speed_ms: float = Field(ge=0.0, le=5.0)
    current_direction_deg: float = Field(ge=0.0, le=360.0)
    corridor_km: float = Field(default=25.0, ge=1.0, le=200.0)
    backtrack_hours: float = Field(default=6.0, ge=0.5, le=72.0)
    step_hours: float = Field(default=0.5, ge=0.1, le=4.0)
    spill_area_m2: float = Field(default=100000.0, ge=1000.0)
    custom_vessels: list[dict[str, Any]] | None = None
    model_id: str | None = None


class EvaluatorInvestigationSummaryItem(BaseModel):
    investigation_id: str
    selected_image_id: str
    image_title: str
    image_path: str
    acquisition_timestamp: str
    model_version: str
    created_at: str
    observation_source: str = "BENCHMARK_FALLBACK"
    top_candidate: dict[str, Any] | None = None

