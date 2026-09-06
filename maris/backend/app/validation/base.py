"""Scientific validation framework for Stage A4.

ScientificValidator is the provider-independent interface.
GenericArtifactValidator implements checks applicable to any AcquiredArtifact.

Provider-specific validators (A4.2–A4.5) will subclass ScientificValidator and
add format/content checks without modifying this module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.acquisition.schemas import AcquiredArtifact
from app.validation.schemas import ValidationIssue, ValidationResult, ValidationSeverity

_ERROR = ValidationSeverity.ERROR
_WARN = ValidationSeverity.WARNING
_INFO = ValidationSeverity.INFO

GENERIC_VALIDATOR_NAME = "generic_artifact_validator"
GENERIC_VALIDATOR_VERSION = "1.0.0"


class ScientificValidator(ABC):
    """Provider-independent validator interface.

    Accepts an AcquiredArtifact and returns a ValidationResult.
    Implementations must be read-only: they must not modify, move, delete,
    or rewrite the artifact.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable validator identifier."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Validator version string."""

    @abstractmethod
    def validate(self, artifact: AcquiredArtifact) -> ValidationResult:
        """Run all checks and return a ValidationResult. Never raises."""


def _issue(severity: ValidationSeverity, code: str, message: str, **details: Any) -> ValidationIssue:
    return ValidationIssue(
        severity=severity,
        code=code,
        message=message,
        details=details or None,
    )


def _result(artifact: AcquiredArtifact, name: str, version: str, issues: list[ValidationIssue]) -> ValidationResult:
    return ValidationResult(
        artifact_location=artifact.location,
        validator_name=name,
        validator_version=version,
        validated_at=datetime.now(timezone.utc),
        issues=issues,
    )


class GenericArtifactValidator(ScientificValidator):
    """Checks applicable to every AcquiredArtifact regardless of provider or format.

    Checks performed (in order, all independent):
    1. artifact location is a non-empty string
    2. artifact path exists on the filesystem
    3. artifact is accessible (readable)
    4. artifact is not empty (size > 0)
    5. provider/source information is present
    6. provenance object is available
    """

    @property
    def name(self) -> str:
        return GENERIC_VALIDATOR_NAME

    @property
    def version(self) -> str:
        return GENERIC_VALIDATOR_VERSION

    def validate(self, artifact: AcquiredArtifact) -> ValidationResult:
        issues: list[ValidationIssue] = []
        issues.extend(self._check_location(artifact))
        issues.extend(self._check_source(artifact))
        issues.extend(self._check_asset_type(artifact))
        issues.extend(self._check_provenance(artifact))
        return _result(artifact, self.name, self.version, issues)

    def _check_location(self, artifact: AcquiredArtifact) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        location = artifact.location
        if not location or not location.strip():
            issues.append(_issue(_ERROR, "ARTIFACT_LOCATION_MISSING", "artifact location is empty or blank"))
            return issues
        path = Path(location)
        if not path.exists():
            issues.append(_issue(_ERROR, "ARTIFACT_NOT_FOUND", "artifact path does not exist", path=location))
            return issues
        try:
            accessible = path.is_file() or path.is_dir()
        except OSError as exc:
            issues.append(_issue(_ERROR, "ARTIFACT_NOT_ACCESSIBLE", "artifact path is not accessible", error=str(exc)))
            return issues
        if not accessible:
            issues.append(_issue(_ERROR, "ARTIFACT_NOT_ACCESSIBLE", "artifact path is not a file or directory"))
            return issues
        try:
            size = path.stat().st_size if path.is_file() else sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        except OSError as exc:
            issues.append(_issue(_ERROR, "ARTIFACT_NOT_ACCESSIBLE", "artifact size could not be determined", error=str(exc)))
            return issues
        if size == 0:
            issues.append(_issue(_ERROR, "ARTIFACT_EMPTY", "artifact exists but contains no data", path=location))
        return issues

    def _check_source(self, artifact: AcquiredArtifact) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not artifact.source or not artifact.source.strip():
            issues.append(_issue(_ERROR, "ARTIFACT_SOURCE_MISSING", "artifact source field is empty"))
        return issues

    def _check_asset_type(self, artifact: AcquiredArtifact) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if artifact.asset_type is None:
            issues.append(_issue(_ERROR, "ARTIFACT_TYPE_MISSING", "artifact asset_type is not set"))
        return issues

    def _check_provenance(self, artifact: AcquiredArtifact) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if artifact.provenance is None:
            issues.append(_issue(_ERROR, "ARTIFACT_PROVENANCE_MISSING", "artifact provenance is not set"))
        return issues
