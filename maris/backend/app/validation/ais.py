"""AIS Position JSON validation for Stage A4.5.

Validates a normalized AIS position artifact (JSON) produced by the A3.5 AIS
acquisition provider contract (maris.ais.positions.v1).

Read-only: the source artifact is never modified.
No AIS intelligence, vessel attribution, suspicious behavior detection,
trajectory reconstruction, interpolation, or drift modelling is performed.

Validation classification stored in ValidationResult.metadata:
  PREFERRED            — no ERRORs, no WARNINGs
  USABLE_WITH_WARNINGS — no ERRORs, at least one WARNING
  INVALID              — at least one ERROR (passed=False)
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from app.acquisition.schemas import AcquiredArtifact
from app.validation.base import ScientificValidator, _issue
from app.validation.schemas import ValidationIssue, ValidationResult, ValidationSeverity

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALIDATOR_NAME = "ais_validator"
VALIDATOR_VERSION = "1.0.0"

_ERROR = ValidationSeverity.ERROR
_WARN = ValidationSeverity.WARNING
_INFO = ValidationSeverity.INFO

EXPECTED_SCHEMA_PREFIX = "maris.ais.positions."

# Classification labels
_CLASS_PREFERRED = "PREFERRED"
_CLASS_USABLE = "USABLE_WITH_WARNINGS"
_CLASS_INVALID = "INVALID"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classify(issues: list[ValidationIssue]) -> str:
    if any(i.severity == _ERROR for i in issues):
        return _CLASS_INVALID
    if any(i.severity == _WARN for i in issues):
        return _CLASS_USABLE
    return _CLASS_PREFERRED


def _parse_timestamp(val: Any) -> datetime | None:
    """Parse timestamp into a UTC datetime if possible."""
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    if isinstance(val, str):
        try:
            # Handle ISO format strings including 'Z' suffix
            clean_str = val.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean_str)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None
    return None


def _is_numeric(val: Any) -> bool:
    """Check if value is a numeric float/int and not a bool."""
    return isinstance(val, (int, float)) and not isinstance(val, bool)


# ---------------------------------------------------------------------------
# Public Validator
# ---------------------------------------------------------------------------

class AisValidator(ScientificValidator):
    """Validates a normalized AIS JSON position artifact (A4.5).

    Checks performed:
    - Artifact exists, is accessible, and is non-empty
    - JSON is syntactically valid
    - Top-level schema is a dictionary/object with expected schema identifier if present
    - Position records collection exists and is a list
    - Position records are non-empty and each record is an object
    - Required fields: timestamp, lat, lon exist and are valid
    - Coordinates: latitude in [-90, +90], longitude in [-180, +180] (signed)
    - Temporal: timestamps parseable, monotonic ordering check, span calculation
    - Identity fields: MMSI (9 digits), IMO (7 digits), vessel_name checked when present
    - Motion fields: SOG (>= 0), COG ([0, 360)), heading ([0, 360)) checked when present
    - Duplicate detection: duplicate position records and duplicate timestamps
    - Metadata population: ranges, field coverage, duplicate counts, classification

    Read-only: the source artifact is never modified.
    """

    @property
    def name(self) -> str:
        return VALIDATOR_NAME

    @property
    def version(self) -> str:
        return VALIDATOR_VERSION

    def validate(self, artifact: AcquiredArtifact) -> ValidationResult:
        issues: list[ValidationIssue] = []
        meta: dict[str, Any] = {}
        path = Path(artifact.location)

        # -------------------------------------------------------------------
        # 1. File Existence & Readability
        # -------------------------------------------------------------------
        if not path.exists():
            issues.append(_issue(
                _ERROR, "AIS_ARTIFACT_NOT_FOUND",
                "artifact file does not exist", path=str(path),
            ))
            meta["validation_classification"] = _CLASS_INVALID
            return self._result(artifact, issues, meta)

        try:
            if path.stat().st_size == 0:
                issues.append(_issue(
                    _ERROR, "AIS_ARTIFACT_EMPTY",
                    "artifact file is empty", path=str(path),
                ))
                meta["validation_classification"] = _CLASS_INVALID
                return self._result(artifact, issues, meta)
        except OSError as exc:
            issues.append(_issue(
                _ERROR, "AIS_ARTIFACT_NOT_ACCESSIBLE",
                f"artifact file size could not be determined: {exc}", path=str(path),
            ))
            meta["validation_classification"] = _CLASS_INVALID
            return self._result(artifact, issues, meta)

        # -------------------------------------------------------------------
        # 2. JSON Parsing & Structure Validation
        # -------------------------------------------------------------------
        try:
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
        except Exception as exc:
            issues.append(_issue(
                _ERROR, "AIS_JSON_INVALID",
                f"artifact JSON could not be parsed: {exc}",
            ))
            meta["validation_classification"] = _CLASS_INVALID
            return self._result(artifact, issues, meta)

        if not isinstance(data, dict):
            issues.append(_issue(
                _ERROR, "AIS_SCHEMA_INVALID",
                "top-level JSON structure must be an object/dictionary",
            ))
            meta["validation_classification"] = _CLASS_INVALID
            return self._result(artifact, issues, meta)

        # Schema identifier check
        schema_val = data.get("schema")
        if schema_val is not None:
            if not isinstance(schema_val, str) or not schema_val.startswith(EXPECTED_SCHEMA_PREFIX):
                issues.append(_issue(
                    _ERROR, "AIS_SCHEMA_INVALID",
                    f"schema identifier '{schema_val}' does not match expected prefix '{EXPECTED_SCHEMA_PREFIX}'",
                ))
            meta["artifact_schema"] = schema_val

        # Records collection check ("records" or "positions")
        records_raw = data.get("records") if "records" in data else data.get("positions")
        if records_raw is None:
            issues.append(_issue(
                _ERROR, "AIS_POSITIONS_MISSING",
                "position records collection ('records' or 'positions') is absent",
            ))
            meta["validation_classification"] = _CLASS_INVALID
            return self._result(artifact, issues, meta)

        if not isinstance(records_raw, list):
            issues.append(_issue(
                _ERROR, "AIS_POSITIONS_NOT_LIST",
                "position records collection is not a list",
            ))
            meta["validation_classification"] = _CLASS_INVALID
            return self._result(artifact, issues, meta)

        if len(records_raw) == 0:
            issues.append(_issue(
                _ERROR, "AIS_POSITIONS_EMPTY",
                "position records collection is empty",
            ))
            meta["position_count"] = 0
            meta["valid_position_count"] = 0
            meta["invalid_position_count"] = 0
            meta["validation_classification"] = _CLASS_INVALID
            return self._result(artifact, issues, meta)

        # -------------------------------------------------------------------
        # 3. Position Records Iteration & Detailed Field Validation
        # -------------------------------------------------------------------
        try:
            valid_records_count = 0
            invalid_records_count = 0

            records_with_mmsi = 0
            records_with_imo = 0
            records_with_vessel_name = 0
            records_with_sog = 0
            records_with_cog = 0
            records_with_heading = 0

            valid_lats: list[float] = []
            valid_lons: list[float] = []
            valid_dts: list[datetime] = []
            valid_sogs: list[float] = []
            valid_cogs: list[float] = []
            valid_headings: list[float] = []

            seen_record_fingerprints: set[str] = set()
            seen_timestamps: set[str] = set()
            duplicate_position_count = 0
            duplicate_timestamp_count = 0

            for index, item in enumerate(records_raw):
                if not isinstance(item, dict):
                    issues.append(_issue(
                        _ERROR, "AIS_RECORD_INVALID",
                        f"record at index {index} is not a dictionary/object",
                    ))
                    invalid_records_count += 1
                    continue

                record_has_error = False

                # --- Duplicate position record check ---
                try:
                    fingerprint = json.dumps(item, sort_keys=True)
                    if fingerprint in seen_record_fingerprints:
                        duplicate_position_count += 1
                        issues.append(_issue(
                            _WARN, "AIS_DUPLICATE_POSITION",
                            f"record at index {index} is a duplicate complete position record",
                        ))
                    else:
                        seen_record_fingerprints.add(fingerprint)
                except Exception:
                    pass

                # --- Required Field: timestamp ---
                raw_ts = item.get("timestamp")
                dt: datetime | None = None
                if raw_ts is None:
                    issues.append(_issue(
                        _ERROR, "AIS_TIMESTAMP_MISSING",
                        f"record at index {index}: required field 'timestamp' is missing",
                    ))
                    record_has_error = True
                else:
                    dt = _parse_timestamp(raw_ts)
                    if dt is None:
                        issues.append(_issue(
                            _ERROR, "AIS_TIMESTAMP_INVALID",
                            f"record at index {index}: timestamp '{raw_ts}' is invalid or cannot be parsed",
                        ))
                        record_has_error = True
                    else:
                        ts_iso = dt.isoformat()
                        if ts_iso in seen_timestamps:
                            duplicate_timestamp_count += 1
                            issues.append(_issue(
                                _WARN, "AIS_DUPLICATE_TIMESTAMP",
                                f"record at index {index}: duplicate timestamp '{ts_iso}'",
                            ))
                        else:
                            seen_timestamps.add(ts_iso)

                # --- Required Field: lat ---
                raw_lat = item.get("lat") if "lat" in item else item.get("latitude")
                lat_float: float | None = None
                if raw_lat is None:
                    issues.append(_issue(
                        _ERROR, "AIS_LATITUDE_INVALID",
                        f"record at index {index}: required field 'lat' is missing",
                    ))
                    record_has_error = True
                elif not _is_numeric(raw_lat):
                    issues.append(_issue(
                        _ERROR, "AIS_LATITUDE_INVALID",
                        f"record at index {index}: latitude '{raw_lat}' is non-numeric",
                    ))
                    record_has_error = True
                else:
                    lat_float = float(raw_lat)
                    if math.isnan(lat_float) or math.isinf(lat_float):
                        issues.append(_issue(
                            _ERROR, "AIS_LATITUDE_INVALID",
                            f"record at index {index}: latitude '{raw_lat}' is non-finite",
                        ))
                        record_has_error = True
                    elif lat_float < -90.0 or lat_float > 90.0:
                        issues.append(_issue(
                            _ERROR, "AIS_LATITUDE_INVALID",
                            f"record at index {index}: latitude {lat_float} is outside valid range [-90, +90]",
                        ))
                        record_has_error = True

                # --- Required Field: lon ---
                raw_lon = item.get("lon") if "lon" in item else item.get("longitude")
                lon_float: float | None = None
                if raw_lon is None:
                    issues.append(_issue(
                        _ERROR, "AIS_LONGITUDE_INVALID",
                        f"record at index {index}: required field 'lon' is missing",
                    ))
                    record_has_error = True
                elif not _is_numeric(raw_lon):
                    issues.append(_issue(
                        _ERROR, "AIS_LONGITUDE_INVALID",
                        f"record at index {index}: longitude '{raw_lon}' is non-numeric",
                    ))
                    record_has_error = True
                else:
                    lon_float = float(raw_lon)
                    if math.isnan(lon_float) or math.isinf(lon_float):
                        issues.append(_issue(
                            _ERROR, "AIS_LONGITUDE_INVALID",
                            f"record at index {index}: longitude '{raw_lon}' is non-finite",
                        ))
                        record_has_error = True
                    elif lon_float < -180.0 or lon_float > 180.0:
                        issues.append(_issue(
                            _ERROR, "AIS_LONGITUDE_INVALID",
                            f"record at index {index}: longitude {lon_float} is outside valid range [-180, +180]",
                        ))
                        record_has_error = True

                # --- Optional Identity Field: MMSI ---
                raw_mmsi = item.get("mmsi")
                if raw_mmsi is None:
                    issues.append(_issue(
                        _WARN, "AIS_OPTIONAL_IDENTITY_MISSING",
                        f"record at index {index}: optional field 'mmsi' is missing",
                    ))
                else:
                    mmsi_str = str(raw_mmsi).strip()
                    if mmsi_str.isdigit() and len(mmsi_str) == 9:
                        records_with_mmsi += 1
                    else:
                        issues.append(_issue(
                            _ERROR, "AIS_MMSI_INVALID",
                            f"record at index {index}: malformed MMSI '{raw_mmsi}' (expected 9 numeric digits)",
                        ))
                        record_has_error = True

                # --- Optional Identity Field: IMO ---
                raw_imo = item.get("imo")
                if raw_imo is None:
                    issues.append(_issue(
                        _WARN, "AIS_OPTIONAL_IDENTITY_MISSING",
                        f"record at index {index}: optional field 'imo' is missing",
                    ))
                else:
                    imo_str = str(raw_imo).strip()
                    imo_digits = imo_str[3:] if imo_str.upper().startswith("IMO") else imo_str
                    if imo_digits.isdigit() and len(imo_digits) == 7:
                        records_with_imo += 1
                    else:
                        issues.append(_issue(
                            _ERROR, "AIS_IMO_INVALID",
                            f"record at index {index}: malformed IMO '{raw_imo}' (expected 7 numeric digits)",
                        ))
                        record_has_error = True

                # --- Optional Identity Field: vessel_name ---
                raw_name = item.get("vessel_name")
                if raw_name is None:
                    issues.append(_issue(
                        _WARN, "AIS_OPTIONAL_IDENTITY_MISSING",
                        f"record at index {index}: optional field 'vessel_name' is missing",
                    ))
                elif not isinstance(raw_name, str) or str(raw_name).strip() == "":
                    issues.append(_issue(
                        _WARN, "AIS_OPTIONAL_IDENTITY_MISSING",
                        f"record at index {index}: 'vessel_name' is empty or blank",
                    ))
                else:
                    records_with_vessel_name += 1

                # --- Optional Motion Field: SOG (speed_over_ground) ---
                raw_sog = item.get("sog") if "sog" in item and item["sog"] is not None else item.get("speed_over_ground")
                if raw_sog is not None:
                    if not _is_numeric(raw_sog):
                        issues.append(_issue(
                            _ERROR, "AIS_SOG_INVALID",
                            f"record at index {index}: SOG '{raw_sog}' is non-numeric",
                        ))
                        record_has_error = True
                    else:
                        sog_float = float(raw_sog)
                        if math.isnan(sog_float) or math.isinf(sog_float) or sog_float < 0.0:
                            issues.append(_issue(
                                _ERROR, "AIS_SOG_INVALID",
                                f"record at index {index}: SOG {sog_float} is invalid or negative",
                            ))
                            record_has_error = True
                        else:
                            records_with_sog += 1
                            valid_sogs.append(sog_float)

                # --- Optional Motion Field: COG (course_over_ground) ---
                raw_cog = item.get("cog") if "cog" in item and item["cog"] is not None else item.get("course_over_ground")
                if raw_cog is not None:
                    if not _is_numeric(raw_cog):
                        issues.append(_issue(
                            _ERROR, "AIS_COG_INVALID",
                            f"record at index {index}: COG '{raw_cog}' is non-numeric",
                        ))
                        record_has_error = True
                    else:
                        cog_float = float(raw_cog)
                        if math.isnan(cog_float) or math.isinf(cog_float) or cog_float < 0.0 or cog_float >= 360.0:
                            issues.append(_issue(
                                _ERROR, "AIS_COG_INVALID",
                                f"record at index {index}: COG {cog_float} is outside valid range [0, 360)",
                            ))
                            record_has_error = True
                        else:
                            records_with_cog += 1
                            valid_cogs.append(cog_float)

                # --- Optional Motion Field: heading ---
                raw_heading = item.get("heading")
                if raw_heading is not None:
                    if not _is_numeric(raw_heading):
                        issues.append(_issue(
                            _ERROR, "AIS_HEADING_INVALID",
                            f"record at index {index}: heading '{raw_heading}' is non-numeric",
                        ))
                        record_has_error = True
                    else:
                        h_float = float(raw_heading)
                        if math.isnan(h_float) or math.isinf(h_float) or h_float < 0.0 or h_float >= 360.0:
                            issues.append(_issue(
                                _ERROR, "AIS_HEADING_INVALID",
                                f"record at index {index}: heading {h_float} is outside valid range [0, 360)",
                            ))
                            record_has_error = True
                        else:
                            records_with_heading += 1
                            valid_headings.append(h_float)

                # --- Optional Motion Field: navigation_status ---
                raw_nav = item.get("navigation_status")
                if raw_nav is None or (isinstance(raw_nav, str) and raw_nav.strip() == ""):
                    issues.append(_issue(
                        _INFO, "AIS_NAVIGATION_STATUS_EMPTY",
                        f"record at index {index}: navigation_status is missing or empty",
                    ))

                # --- Record-level Accounting ---
                if record_has_error:
                    invalid_records_count += 1
                else:
                    valid_records_count += 1
                    if lat_float is not None:
                        valid_lats.append(lat_float)
                    if lon_float is not None:
                        valid_lons.append(lon_float)
                    if dt is not None:
                        valid_dts.append(dt)

            # ---------------------------------------------------------------
            # 4. Summary & Aggregate Validations
            # ---------------------------------------------------------------
            total_records = len(records_raw)
            meta["position_count"] = total_records
            meta["valid_position_count"] = valid_records_count
            meta["invalid_position_count"] = invalid_records_count

            meta["duplicate_position_count"] = duplicate_position_count
            meta["duplicate_timestamp_count"] = duplicate_timestamp_count

            meta["records_with_mmsi"] = records_with_mmsi
            meta["records_with_imo"] = records_with_imo
            meta["records_with_vessel_name"] = records_with_vessel_name
            meta["records_with_sog"] = records_with_sog
            meta["records_with_cog"] = records_with_cog
            meta["records_with_heading"] = records_with_heading

            if valid_records_count == 0:
                issues.append(_issue(
                    _ERROR, "AIS_ALL_RECORDS_INVALID",
                    "no valid position records remain in the artifact",
                ))
            elif invalid_records_count > 0:
                issues.append(_issue(
                    _ERROR, "AIS_PARTIAL_INVALID_RECORDS",
                    f"{invalid_records_count} of {total_records} position records are invalid",
                ))

            if valid_lats:
                meta["latitude_min"] = min(valid_lats)
                meta["latitude_max"] = max(valid_lats)

            if valid_lons:
                meta["longitude_min"] = min(valid_lons)
                meta["longitude_max"] = max(valid_lons)

            if valid_sogs:
                meta["sog_min"] = min(valid_sogs)
                meta["sog_max"] = max(valid_sogs)

            if valid_cogs:
                meta["cog_min"] = min(valid_cogs)
                meta["cog_max"] = max(valid_cogs)

            if valid_headings:
                meta["heading_min"] = min(valid_headings)
                meta["heading_max"] = max(valid_headings)

            if valid_dts:
                min_dt = min(valid_dts)
                max_dt = max(valid_dts)
                meta["time_start"] = min_dt.isoformat()
                meta["time_end"] = max_dt.isoformat()
                meta["time_count"] = len(valid_dts)
                meta["temporal_span_seconds"] = (max_dt - min_dt).total_seconds()

                if len(valid_dts) > 1:
                    is_ascending = all(valid_dts[i] <= valid_dts[i + 1] for i in range(len(valid_dts) - 1))
                    is_descending = all(valid_dts[i] >= valid_dts[i + 1] for i in range(len(valid_dts) - 1))
                    if is_ascending:
                        meta["temporal_order"] = "ascending"
                    elif is_descending:
                        meta["temporal_order"] = "descending"
                        issues.append(_issue(
                            _WARN, "AIS_TIMESTAMP_NONMONOTONIC",
                            "timestamps are in descending chronological order",
                        ))
                    else:
                        meta["temporal_order"] = "non_monotonic"
                        issues.append(_issue(
                            _WARN, "AIS_TIMESTAMP_NONMONOTONIC",
                            "timestamps are not chronologically ordered",
                        ))
                else:
                    meta["temporal_order"] = "single_value"


        except Exception as exc:
            # Fail closed on unexpected errors
            issues.append(_issue(
                _ERROR, "AIS_INTERNAL_ERROR",
                f"unexpected error during AIS validation: {exc}",
            ))

        meta["validation_classification"] = _classify(issues)
        return self._result(artifact, issues, meta)

    def _result(
        self,
        artifact: AcquiredArtifact,
        issues: list[ValidationIssue],
        meta: dict[str, Any],
    ) -> ValidationResult:
        return ValidationResult(
            artifact_location=artifact.location,
            validator_name=self.name,
            validator_version=self.version,
            validated_at=datetime.now(timezone.utc),
            issues=issues,
            metadata=meta,
        )
