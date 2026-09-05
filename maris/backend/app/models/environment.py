from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.common import BoundingBox, EnvironmentKind, TimeWindow


class Environment(BaseModel):
    """Metocean field (wind, current, or other) for an investigation, backed by a stored asset."""

    id: str
    investigation_id: str
    asset_id: str
    kind: EnvironmentKind
    provider: str
    time_window: TimeWindow
    spatial_bounds: BoundingBox
    variables: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WindField(Environment):
    kind: Literal[EnvironmentKind.WIND] = EnvironmentKind.WIND


class CurrentField(Environment):
    kind: Literal[EnvironmentKind.CURRENT] = EnvironmentKind.CURRENT
