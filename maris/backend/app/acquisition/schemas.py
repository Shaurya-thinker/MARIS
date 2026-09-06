from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.models.common import AreaOfInterest, AssetType, Provenance, TimeWindow


class AcquisitionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProviderIdentity(BaseModel):
    """Who a provider is and which asset types it may produce. No network details."""

    id: str
    name: str
    supported_asset_types: list[AssetType] = Field(default_factory=list)


class AcquisitionRequest(BaseModel):
    """Provider-independent request to obtain data for an investigation."""

    investigation_id: str
    provider_id: str
    asset_type: AssetType
    area_of_interest: AreaOfInterest | None = None
    time_window: TimeWindow | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AcquiredArtifact(BaseModel):
    """Provider output before it is registered as a domain Asset."""

    asset_type: AssetType
    location: str
    source: str
    acquisition_time: datetime | None = None
    provenance: Provenance = Field(default_factory=Provenance)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AcquisitionResult(BaseModel):
    artifacts: list[AcquiredArtifact] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AcquisitionJob(BaseModel):
    """Lifecycle record for one acquisition request. Persistence is out of scope."""

    id: str
    request: AcquisitionRequest
    status: AcquisitionStatus = AcquisitionStatus.PENDING
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    asset_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
