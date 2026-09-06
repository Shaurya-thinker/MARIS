"""Stage A4 scientific validation framework.

Public surface:
- ScientificValidator   abstract validator interface
- GenericArtifactValidator  provider-independent generic checks
- ValidationResult      outcome of one validator run
- ValidationIssue       a single finding
- ValidationSeverity    INFO / WARNING / ERROR
"""

from app.validation.base import GenericArtifactValidator, ScientificValidator
from app.validation.schemas import ValidationIssue, ValidationResult, ValidationSeverity

__all__ = [
    "GenericArtifactValidator",
    "ScientificValidator",
    "ValidationIssue",
    "ValidationResult",
    "ValidationSeverity",
]
