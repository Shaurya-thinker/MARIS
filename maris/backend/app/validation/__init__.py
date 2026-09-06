"""Stage A4 scientific validation framework.

Public surface:
- ScientificValidator        abstract validator interface
- GenericArtifactValidator   provider-independent generic checks
- Sentinel1Validator         Sentinel-1 ZIP/SAFE product validator (A4.2)
- ValidationResult           outcome of one validator run
- ValidationIssue            a single finding
- ValidationSeverity         INFO / WARNING / ERROR
"""

from app.validation.base import GenericArtifactValidator, ScientificValidator
from app.validation.schemas import ValidationIssue, ValidationResult, ValidationSeverity
from app.validation.sentinel1 import Sentinel1Validator

__all__ = [
    "GenericArtifactValidator",
    "ScientificValidator",
    "Sentinel1Validator",
    "ValidationIssue",
    "ValidationResult",
    "ValidationSeverity",
]
