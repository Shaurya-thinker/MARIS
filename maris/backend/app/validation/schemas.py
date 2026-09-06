"""Validation result contracts for Stage A4.

These types are provider-independent. They carry no knowledge of Sentinel-1,
ERA5, CMEMS, AIS, NetCDF, GeoTIFF, or any other format.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ValidationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ValidationIssue(BaseModel):
    """A single finding produced by a validator."""

    severity: ValidationSeverity
    code: str
    message: str
    target: str | None = None
    details: dict[str, Any] | None = None


class ValidationResult(BaseModel):
    """Outcome of one validator run against one artifact.

    passed is True when no ERROR issues are present.
    WARNING and INFO issues do not affect passed.
    """

    artifact_location: str
    validator_name: str
    validator_version: str
    validated_at: datetime
    issues: list[ValidationIssue] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    passed: bool = True

    @model_validator(mode="after")
    def _sync_passed(self) -> "ValidationResult":
        self.passed = not any(issue.severity == ValidationSeverity.ERROR for issue in self.issues)
        return self
