"""Pydantic schemas for the real-data experiment API endpoints.

These models live exclusively in the API layer and are NOT imported by any
existing domain service.  They translate between HTTP request/response bodies
and the service layer DTOs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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
    west: float = Field(ge=-180.0, le=180.0)
    south: float = Field(ge=-90.0, le=90.0)
    east: float = Field(ge=-180.0, le=180.0)
    north: float = Field(ge=-90.0, le=90.0)
    start: datetime
    end: datetime
    investigation_id: str = Field(default="real-experiment")


class VesselSummaryItem(BaseModel):
    mmsi: str | None
    vessel_name: str | None
    imo: str | None
    position_count: int
    first_timestamp: str
    last_timestamp: str
    source_adapter: str


class AisSearchResponse(BaseModel):
    vessels: list[VesselSummaryItem]
    total_positions: int
    search_bbox: dict[str, float]
    search_window_start: str
    search_window_end: str
    adapter_id: str
    configured: bool


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


class EvaluatorDriftPreviewResponse(BaseModel):
    observation_point: dict[str, float]
    observation_time: str
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
    top_candidate: dict[str, Any] | None = None

