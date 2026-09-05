from pydantic import BaseModel


class Evidence(BaseModel):
    id: str
    type: str
    source: str
    confidence: float
    explanation: str