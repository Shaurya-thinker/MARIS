from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.common import AreaOfInterest, InvestigationStatus, TimeWindow


class Investigation(BaseModel):
    """A case-agnostic spill investigation. Assets and evidence are referenced by id."""

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
