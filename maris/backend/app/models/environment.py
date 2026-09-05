from datetime import datetime
from typing import Any

from pydantic import BaseModel


class WindField(BaseModel):
    provider: str
    time_range: tuple[datetime, datetime]
    spatial_bounds: tuple[float, float, float, float]
    variables: dict[str, Any]


class CurrentField(BaseModel):
    provider: str
    time_range: tuple[datetime, datetime]
    spatial_bounds: tuple[float, float, float, float]
    variables: dict[str, Any]