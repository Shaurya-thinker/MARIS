from datetime import datetime

from pydantic import BaseModel


class VesselTrack(BaseModel):
    vessel_id: str
    positions: list[tuple[float, float]]
    timestamps: list[datetime]
    speed: list[float]
    heading: list[float]