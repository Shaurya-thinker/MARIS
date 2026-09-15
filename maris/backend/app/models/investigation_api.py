"""Stage G1 — Investigation API schemas and data contracts.

Defines request/response models for investigation lifecycle, status tracking,
workflow orchestration, and artifact provenance inspection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.common import AreaOfInterest, AssetType, InvestigationStatus, Provenance, TimeWindow


class StageError(BaseModel):
    """Structured error surfaced during pipeline stage execution."""

    stage: str
    error: str
    message: str


class InvestigationCreateRequest(BaseModel):
    """Payload to initialize a new spill investigation."""

    name: str = Field(..., min_length=1, max_length=500, description="Investigation title or name")
    area_of_interest: AreaOfInterest = Field(..., description="Geographic bounding box or GeoJSON polygon")
    time_window: TimeWindow = Field(..., description="Investigation temporal bounds")
    description: str | None = Field(default=None, max_length=5000, description="Optional narrative context or notes")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Arbitrary domain metadata")


class InvestigationResponse(BaseModel):
    """Structured response detailing an investigation record."""

    id: str
    name: str
    status: InvestigationStatus
    area_of_interest: AreaOfInterest
    time_window: TimeWindow
    created_at: datetime
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    asset_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class InvestigationListItem(BaseModel):
    """Lightweight investigation summary for listing endpoints."""

    id: str
    name: str
    status: InvestigationStatus
    created_at: datetime
    description: str | None = None
    asset_count: int = 0


class InvestigationStatusResponse(BaseModel):
    """Structured processing state of an investigation workflow."""

    investigation_id: str
    status: InvestigationStatus
    current_stage: str | None = None
    completed_stages: list[str] = Field(default_factory=list)
    available_artifacts: list[str] = Field(default_factory=list)
    errors: list[StageError] = Field(default_factory=list)


class InvestigationRunRequest(BaseModel):
    """Parameters passed to orchestrate pipeline stages B1 through F3."""

    # Stage B1/B2/B3 SAR inputs
    sentinel1_artifact_path: str | None = Field(
        default=None,
        description="Local path to Sentinel-1 raw ZIP artifact (for B1 ingestion)",
    )
    sar_asset_id: str | None = Field(
        default=None,
        description="Registered SAR asset ID (B2 preprocessed or B1 ingested)",
    )
    spill_id: str | None = Field(
        default=None,
        description="Registered spill detection asset ID or product ID (for D1/D3/E1/E2/E3/F1/F2/F3)",
    )
    scene_id: str | None = Field(
        default=None,
        description="Satellite scene identifier for C1 environmental acquisition",
    )

    # Stage C1 / D1 / D3 Environmental forcing
    wind_asset_id: str | None = Field(
        default=None,
        description="Registered C1 ENVIRONMENT_WIND Asset (ERA5 NetCDF)",
    )
    current_asset_id: str | None = Field(
        default=None,
        description="Registered C1 ENVIRONMENT_CURRENT Asset (CMEMS NetCDF)",
    )

    # Stage E1 AIS inputs
    ais_asset_id: str | None = Field(
        default=None,
        description="Registered AIS Asset ID or path (optional)",
    )

    # Tuning parameters
    polarization: str | None = Field(default=None, description="SAR polarization preference ('VV', 'VH')")
    drift_hours: float | None = Field(default=None, description="Forward drift horizon in hours (D1)")
    lookback_hours: float | None = Field(default=None, description="Backward drift horizon in hours (D3)")
    step_hours: float | None = Field(default=None, description="Integration step in hours (D1/D3)")
    leeway_fraction: float | None = Field(default=None, description="Wind leeway factor alpha (D1/D3)")
    temporal_window_hours: float | None = Field(default=None, description="AIS temporal candidate window (E1)")
    spatial_buffer_km: float | None = Field(default=None, description="AIS spatial buffer in km (E1)")


class InvestigationRunResponse(BaseModel):
    """Response returned upon completing or failing a workflow execution."""

    investigation_id: str
    status: InvestigationStatus
    current_stage: str | None = None
    completed_stages: list[str] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of stage or artifact name to registered Asset ID",
    )
    errors: list[StageError] = Field(default_factory=list)


class ArtifactSummary(BaseModel):
    """Inspection summary of an artifact registered in AssetRegistry."""

    asset_id: str
    investigation_id: str
    asset_type: AssetType
    provider: str
    source: str
    location: str
    acquisition_time: datetime | None = None
    processing_level: str | None = None
    validation_status: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)
    upstream_asset_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
