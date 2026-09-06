"""Tests for Stage A4.1 — Generic Artifact Validation Framework.

All tests are offline. No network calls, no provider credentials.
Temporary files are used where filesystem presence is required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from app.acquisition.schemas import AcquiredArtifact
from app.models.common import AssetType, Provenance
from app.validation import (
    GenericArtifactValidator,
    ScientificValidator,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)
from app.validation.base import GENERIC_VALIDATOR_NAME, GENERIC_VALIDATOR_VERSION
from app.validation.schemas import ValidationSeverity as Sev


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _artifact(**overrides: object) -> AcquiredArtifact:
    """Return a minimal valid AcquiredArtifact. Override any field via kwargs."""
    payload: dict[str, object] = {
        "asset_type": AssetType.ENVIRONMENT_WIND,
        "location": "/tmp/placeholder",
        "source": "test-source",
        "provenance": Provenance(),
    }
    payload.update(overrides)
    return AcquiredArtifact.model_validate(payload)


def _validator() -> GenericArtifactValidator:
    return GenericArtifactValidator()


def _codes(result: ValidationResult) -> list[str]:
    return [issue.code for issue in result.issues]


def _severities(result: ValidationResult) -> list[ValidationSeverity]:
    return [issue.severity for issue in result.issues]


# ---------------------------------------------------------------------------
# Schema contract tests
# ---------------------------------------------------------------------------

class ValidationSeverityTests(unittest.TestCase):
    def test_severity_values(self) -> None:
        self.assertEqual(Sev.INFO.value, "info")
        self.assertEqual(Sev.WARNING.value, "warning")
        self.assertEqual(Sev.ERROR.value, "error")

    def test_severity_is_string_enum(self) -> None:
        self.assertIsInstance(Sev.ERROR, str)


class ValidationIssueTests(unittest.TestCase):
    def test_minimal_issue(self) -> None:
        issue = ValidationIssue(severity=Sev.ERROR, code="TEST_CODE", message="test message")
        self.assertIsNone(issue.target)
        self.assertIsNone(issue.details)

    def test_full_issue(self) -> None:
        issue = ValidationIssue(
            severity=Sev.WARNING,
            code="WARN_CODE",
            message="a warning",
            target="some.field",
            details={"key": "value"},
        )
        self.assertEqual(issue.target, "some.field")
        self.assertEqual(issue.details, {"key": "value"})


class ValidationResultTests(unittest.TestCase):
    def test_no_issues_passes(self) -> None:
        result = ValidationResult(
            artifact_location="/tmp/x",
            validator_name="v",
            validator_version="1",
            validated_at=datetime.now(timezone.utc),
        )
        self.assertTrue(result.passed)

    def test_error_issue_fails(self) -> None:
        result = ValidationResult(
            artifact_location="/tmp/x",
            validator_name="v",
            validator_version="1",
            validated_at=datetime.now(timezone.utc),
            issues=[ValidationIssue(severity=Sev.ERROR, code="E", message="err")],
        )
        self.assertFalse(result.passed)

    def test_warning_only_passes(self) -> None:
        result = ValidationResult(
            artifact_location="/tmp/x",
            validator_name="v",
            validator_version="1",
            validated_at=datetime.now(timezone.utc),
            issues=[ValidationIssue(severity=Sev.WARNING, code="W", message="warn")],
        )
        self.assertTrue(result.passed)

    def test_info_only_passes(self) -> None:
        result = ValidationResult(
            artifact_location="/tmp/x",
            validator_name="v",
            validator_version="1",
            validated_at=datetime.now(timezone.utc),
            issues=[ValidationIssue(severity=Sev.INFO, code="I", message="info")],
        )
        self.assertTrue(result.passed)

    def test_mixed_error_and_warning_fails(self) -> None:
        result = ValidationResult(
            artifact_location="/tmp/x",
            validator_name="v",
            validator_version="1",
            validated_at=datetime.now(timezone.utc),
            issues=[
                ValidationIssue(severity=Sev.WARNING, code="W", message="warn"),
                ValidationIssue(severity=Sev.ERROR, code="E", message="err"),
            ],
        )
        self.assertFalse(result.passed)

    def test_passed_is_deterministic(self) -> None:
        """Same issues always produce the same passed value."""
        issues = [ValidationIssue(severity=Sev.ERROR, code="E", message="e")]
        for _ in range(5):
            result = ValidationResult(
                artifact_location="/tmp/x",
                validator_name="v",
                validator_version="1",
                validated_at=datetime.now(timezone.utc),
                issues=issues,
            )
            self.assertFalse(result.passed)


# ---------------------------------------------------------------------------
# ScientificValidator interface tests
# ---------------------------------------------------------------------------

class ScientificValidatorInterfaceTests(unittest.TestCase):
    def test_generic_validator_is_scientific_validator(self) -> None:
        self.assertIsInstance(_validator(), ScientificValidator)

    def test_name_and_version(self) -> None:
        v = _validator()
        self.assertEqual(v.name, GENERIC_VALIDATOR_NAME)
        self.assertEqual(v.version, GENERIC_VALIDATOR_VERSION)

    def test_name_is_stable(self) -> None:
        self.assertEqual(_validator().name, _validator().name)

    def test_version_is_stable(self) -> None:
        self.assertEqual(_validator().version, _validator().version)


# ---------------------------------------------------------------------------
# Generic validation — passing cases
# ---------------------------------------------------------------------------

class GenericValidatorPassTests(unittest.TestCase):
    def test_valid_file_artifact_passes(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"content")
            tmp_path = tmp.name
        try:
            artifact = _artifact(location=tmp_path)
            result = _validator().validate(artifact)
            self.assertTrue(result.passed)
            self.assertEqual(result.issues, [])
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_valid_directory_artifact_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            (Path(tmp_dir) / "data.nc").write_bytes(b"CDF\x01mock")
            artifact = _artifact(location=tmp_dir)
            result = _validator().validate(artifact)
            self.assertTrue(result.passed)

    def test_result_records_artifact_location(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"x")
            tmp_path = tmp.name
        try:
            artifact = _artifact(location=tmp_path)
            result = _validator().validate(artifact)
            self.assertEqual(result.artifact_location, tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Generic validation — location checks
# ---------------------------------------------------------------------------

class GenericValidatorLocationTests(unittest.TestCase):
    def test_missing_file_fails(self) -> None:
        artifact = _artifact(location="/nonexistent/path/artifact.nc")
        result = _validator().validate(artifact)
        self.assertFalse(result.passed)
        self.assertIn("ARTIFACT_NOT_FOUND", _codes(result))

    def test_empty_location_string_fails(self) -> None:
        artifact = _artifact(location="")
        result = _validator().validate(artifact)
        self.assertFalse(result.passed)
        self.assertIn("ARTIFACT_LOCATION_MISSING", _codes(result))

    def test_blank_location_string_fails(self) -> None:
        artifact = _artifact(location="   ")
        result = _validator().validate(artifact)
        self.assertFalse(result.passed)
        self.assertIn("ARTIFACT_LOCATION_MISSING", _codes(result))

    def test_empty_file_fails(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
        try:
            artifact = _artifact(location=tmp_path)
            result = _validator().validate(artifact)
            self.assertFalse(result.passed)
            self.assertIn("ARTIFACT_EMPTY", _codes(result))
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_empty_directory_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact = _artifact(location=tmp_dir)
            result = _validator().validate(artifact)
            self.assertFalse(result.passed)
            self.assertIn("ARTIFACT_EMPTY", _codes(result))

    def test_missing_artifact_error_is_error_severity(self) -> None:
        artifact = _artifact(location="/nonexistent/artifact.nc")
        result = _validator().validate(artifact)
        error_issues = [i for i in result.issues if i.code == "ARTIFACT_NOT_FOUND"]
        self.assertTrue(error_issues)
        self.assertEqual(error_issues[0].severity, Sev.ERROR)


# ---------------------------------------------------------------------------
# Generic validation — source checks
# ---------------------------------------------------------------------------

class GenericValidatorSourceTests(unittest.TestCase):
    def test_missing_source_fails(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"data")
            tmp_path = tmp.name
        try:
            artifact = _artifact(location=tmp_path, source="")
            result = _validator().validate(artifact)
            self.assertFalse(result.passed)
            self.assertIn("ARTIFACT_SOURCE_MISSING", _codes(result))
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_blank_source_fails(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"data")
            tmp_path = tmp.name
        try:
            artifact = _artifact(location=tmp_path, source="   ")
            result = _validator().validate(artifact)
            self.assertFalse(result.passed)
            self.assertIn("ARTIFACT_SOURCE_MISSING", _codes(result))
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_source_missing_is_error_severity(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"data")
            tmp_path = tmp.name
        try:
            artifact = _artifact(location=tmp_path, source="")
            result = _validator().validate(artifact)
            source_issues = [i for i in result.issues if i.code == "ARTIFACT_SOURCE_MISSING"]
            self.assertTrue(source_issues)
            self.assertEqual(source_issues[0].severity, Sev.ERROR)
        finally:
            Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Generic validation — provenance checks
# ---------------------------------------------------------------------------

class GenericValidatorProvenanceTests(unittest.TestCase):
    def test_provenance_present_passes(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"data")
            tmp_path = tmp.name
        try:
            artifact = _artifact(location=tmp_path, provenance=Provenance(product_id="pid-1"))
            result = _validator().validate(artifact)
            self.assertTrue(result.passed)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_default_provenance_passes(self) -> None:
        """Provenance() with all-None fields is still a valid provenance object."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"data")
            tmp_path = tmp.name
        try:
            artifact = _artifact(location=tmp_path, provenance=Provenance())
            result = _validator().validate(artifact)
            self.assertTrue(result.passed)
        finally:
            Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Generic validation — all checks are independent
# ---------------------------------------------------------------------------

class GenericValidatorIndependenceTests(unittest.TestCase):
    def test_multiple_failures_all_reported(self) -> None:
        """Missing file + missing source both appear in issues."""
        artifact = _artifact(location="/nonexistent/artifact.nc", source="")
        result = _validator().validate(artifact)
        self.assertFalse(result.passed)
        codes = _codes(result)
        self.assertIn("ARTIFACT_NOT_FOUND", codes)
        self.assertIn("ARTIFACT_SOURCE_MISSING", codes)

    def test_location_failure_does_not_suppress_source_check(self) -> None:
        artifact = _artifact(location="", source="")
        result = _validator().validate(artifact)
        codes = _codes(result)
        self.assertIn("ARTIFACT_LOCATION_MISSING", codes)
        self.assertIn("ARTIFACT_SOURCE_MISSING", codes)


# ---------------------------------------------------------------------------
# Validator metadata
# ---------------------------------------------------------------------------

class ValidatorMetadataTests(unittest.TestCase):
    def test_result_records_validator_name(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"x")
            tmp_path = tmp.name
        try:
            result = _validator().validate(_artifact(location=tmp_path))
            self.assertEqual(result.validator_name, GENERIC_VALIDATOR_NAME)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_result_records_validator_version(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"x")
            tmp_path = tmp.name
        try:
            result = _validator().validate(_artifact(location=tmp_path))
            self.assertEqual(result.validator_version, GENERIC_VALIDATOR_VERSION)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_result_records_validated_at(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"x")
            tmp_path = tmp.name
        try:
            before = datetime.now(timezone.utc)
            result = _validator().validate(_artifact(location=tmp_path))
            after = datetime.now(timezone.utc)
            self.assertIsNotNone(result.validated_at)
            self.assertGreaterEqual(result.validated_at, before)
            self.assertLessEqual(result.validated_at, after)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_result_contains_no_credentials(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"x")
            tmp_path = tmp.name
        try:
            result = _validator().validate(_artifact(location=tmp_path))
            dumped = str(result.model_dump())
            for secret_word in ("password", "api_key", "access_token", "secret"):
                self.assertNotIn(secret_word, dumped.lower())
        finally:
            Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Read-only guarantee
# ---------------------------------------------------------------------------

class ValidatorReadOnlyTests(unittest.TestCase):
    def test_validation_does_not_modify_file(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"original content")
            tmp_path = tmp.name
        try:
            before_mtime = Path(tmp_path).stat().st_mtime
            before_size = Path(tmp_path).stat().st_size
            _validator().validate(_artifact(location=tmp_path))
            after_mtime = Path(tmp_path).stat().st_mtime
            after_size = Path(tmp_path).stat().st_size
            self.assertEqual(before_mtime, after_mtime)
            self.assertEqual(before_size, after_size)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_validation_does_not_delete_file(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"data")
            tmp_path = tmp.name
        try:
            _validator().validate(_artifact(location=tmp_path))
            self.assertTrue(Path(tmp_path).exists())
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_validation_of_missing_artifact_does_not_create_file(self) -> None:
        path = "/tmp/maris_should_not_exist_a4test.nc"
        Path(path).unlink(missing_ok=True)
        try:
            _validator().validate(_artifact(location=path))
            self.assertFalse(Path(path).exists())
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Issue code determinism
# ---------------------------------------------------------------------------

class IssueDeterminismTests(unittest.TestCase):
    def test_same_input_same_codes(self) -> None:
        artifact = _artifact(location="/nonexistent/path.nc", source="")
        codes_first = _codes(_validator().validate(artifact))
        codes_second = _codes(_validator().validate(artifact))
        self.assertEqual(codes_first, codes_second)

    def test_warning_does_not_fail_passed(self) -> None:
        """Explicit regression: a WARNING issue must not set passed=False."""
        result = ValidationResult(
            artifact_location="/tmp/x",
            validator_name="test",
            validator_version="0",
            validated_at=datetime.now(timezone.utc),
            issues=[ValidationIssue(severity=Sev.WARNING, code="WARN", message="w")],
        )
        self.assertTrue(result.passed)

    def test_error_always_fails_passed(self) -> None:
        result = ValidationResult(
            artifact_location="/tmp/x",
            validator_name="test",
            validator_version="0",
            validated_at=datetime.now(timezone.utc),
            issues=[ValidationIssue(severity=Sev.ERROR, code="ERR", message="e")],
        )
        self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
