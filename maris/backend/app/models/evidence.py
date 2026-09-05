from typing import Any

from pydantic import BaseModel, Field

from app.models.common import EvidenceType


class Evidence(BaseModel):
    """A single evidence item attached to an investigation, optionally citing assets."""

    id: str
    investigation_id: str
    type: EvidenceType
    source: str
    explanation: str
    asset_ids: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
