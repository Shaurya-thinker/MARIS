from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.common import AssetType, Provenance


class Asset(BaseModel):
    """Stored investigation artifact (file, URI, or derived product)."""

    id: str
    investigation_id: str
    type: AssetType
    provider: str
    source: str
    location: str = Field(description="Filesystem path or URI for the stored artifact")
    acquisition_time: datetime | None = None
    provenance: Provenance = Field(default_factory=Provenance)
    metadata: dict[str, Any] = Field(default_factory=dict)
