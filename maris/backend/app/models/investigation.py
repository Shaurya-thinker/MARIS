from datetime import datetime

from pydantic import BaseModel


class Investigation(BaseModel):
    id: str
    name: str
    created_at: datetime
    status: str
    description: str | None = None