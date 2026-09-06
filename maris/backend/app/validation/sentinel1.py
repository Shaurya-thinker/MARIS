"""Sentinel-1 product validation for Stage A4.2.

Inspects a Sentinel-1 ZIP/SAFE artifact using stdlib zipfile and
xml.etree.ElementTree.  No GDAL, rasterio, xarray, numpy, or SNAP.

Validation is read-only: the source artifact is never modified, moved,
deleted, or rewritten.  Temporary extraction is not used; manifest.safe
is read directly from the ZIP central directory.

Validation classification (stored in ValidationResult.metadata):
  PREFERRED            — no ERRORs, no relevant WARNINGs
  USABLE_WITH_WARNINGS — no ERRORs, at least one WARNING
  INVALID              — at least one ERROR  (passed=False)
"""

from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.acquisition.schemas import AcquiredArtifact
from app.validation.base import ScientificValidator, _issue
from app.validation.schemas import ValidationIssue, ValidationResult, ValidationSeverity

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALIDATOR_NAME = "sentinel1_validator"
VALIDATOR_VERSION = "1.0.0"

_ERROR = ValidationSeverity.ERROR
_WARN = ValidationSeverity.WARNING
_INFO = ValidationSeverity.INFO

# manifest.safe is always at the root of the SAFE directory inside the ZIP
_MANIFEST_SUFFIX = "manifest.safe"

# XML namespaces used in manifest.safe (ESA XFDU / SAFE spec)
_NS: dict[str, str] = {
    "xfdu": "urn:ccsds:schema:xfdu:1",
    "safe": "http://www.esa.int/safe/sentinel/1.1",
    "s1": "http://www.esa.int/safe/sentinel-1.0",
    "s1sarl1": "http://www.esa.int/safe/sentinel-1.0/sentinel-1/sar/level-1",
    "gml": "http://www.opengis.net/gml",
}

# Platform family name as it appears in manifest.safe
_SENTINEL1_FAMILY = "SENTINEL-1"
# SAR instrument abbreviation
_SAR_ABBREVIATION = "SAR"

# Recognised Sentinel-1 platform numbers (A, B; C launched 2024)
_KNOWN_PLATFORM_NUMBERS = {"A", "B", "C"}

# Product type → (is_valid, classification_hint, warning_message_or_None)
# GRD covers both GRDH and GRDM; the distinction comes from the product name,
# not the manifest productType field (which is always "GRD" for both).
_PRODUCT_TYPE_TABLE: dict[str, tuple[bool, str, str | None]] = {
    "GRD": (True, "preferred", None),
    "SLC": (True, "usable", "IW SLC requires SLC-specific processing before intensity analysis"),
    "RAW": (True, "usable", "RAW product requires range-Doppler focusing before use"),
    "OCN": (True, "usable", "OCN product is a Level-2 ocean product, not a SAR image"),
}

# Recognised sensor modes and their classification hint
_MODE_TABLE: dict[str, tuple[str, str | None]] = {
    "IW": ("preferred", None),
    "EW": ("usable", "EW mode has coarser resolution than IW; verify suitability"),
    "SM": ("usable", "SM (Stripmap) mode; verify suitability for intended processing"),
    "WV": ("usable", "WV (Wave) mode acquires small vignettes; verify suitability"),
}

# Processing level derived from product name field (parts[2] in CDSE naming)
# GRDH → preferred, GRDM → usable with warning
_PROC_LEVEL_WARNINGS: dict[str, str | None] = {
    "GRDH": None,
    "GRDM": "GRDM has medium resolution (40 m); GRDH (10 m) is preferred",
    "SLC_": None,  # SLC warning already emitted via product type
}

# Classification labels stored in metadata
_CLASS_PREFERRED = "PREFERRED"
_CLASS_USABLE = "USABLE_WITH_WARNINGS"
_CLASS_INVALID = "INVALID"


# ---------------------------------------------------------------------------
# Internal data container
# ---------------------------------------------------------------------------

class _ManifestData:
    """Parsed fields from manifest.safe.  All fields are optional strings."""

    product_type: str | None = None
    platform_family: str | None = None
    platform_number: str | None = None
    instrument_family: str | None = None
    instrument_abbreviation: str | None = None
    sensor_mode: str | None = None
    polarisation: str | None = None
    sensing_start: str | None = None
    sensing_stop: str | None = None
    orbit_number: str | None = None
    footprint_coords: str | None = None
    # product name derived from the ZIP entry path (SAFE directory name)
    product_name: str | None = None


# ---------------------------------------------------------------------------
# ZIP / archive helpers
# ---------------------------------------------------------------------------

def _open_zip(path: Path) -> tuple[zipfile.ZipFile | None, list[ValidationIssue]]:
    """Try to open the ZIP.  Returns (zf, []) on success or (None, [issue]) on failure."""
    issues: list[ValidationIssue] = []
    if not path.exists():
        issues.append(_issue(_ERROR, "S1_ARTIFACT_NOT_FOUND", "artifact file does not exist", path=str(path)))
        return None, issues
    if path.stat().st_size == 0:
        issues.append(_issue(_ERROR, "S1_ARTIFACT_EMPTY", "artifact file is empty", path=str(path)))
        return None, issues
    try:
        zf = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile as exc:
        issues.append(_issue(_ERROR, "S1_ZIP_CORRUPT", f"ZIP cannot be opened: {exc}", path=str(path)))
        return None, issues
    except OSError as exc:
        issues.append(_issue(_ERROR, "S1_ZIP_INACCESSIBLE", f"ZIP cannot be read: {exc}", path=str(path)))
        return None, issues
    # Test the ZIP integrity (CRC checks on all entries)
    bad = zf.testzip()
    if bad is not None:
        zf.close()
        issues.append(_issue(_ERROR, "S1_ZIP_CORRUPT", f"ZIP CRC failure in entry: {bad}", path=str(path)))
        return None, issues
    return zf, issues


def _find_manifest(zf: zipfile.ZipFile) -> tuple[str | None, list[ValidationIssue]]:
    """Locate manifest.safe inside the ZIP.  Returns (name, []) or (None, [issue])."""
    issues: list[ValidationIssue] = []
    names = zf.namelist()
    # manifest.safe sits at <SAFE_dir>/manifest.safe
    candidates = [n for n in names if n.endswith(_MANIFEST_SUFFIX) and n.count("/") <= 1]
    if not candidates:
        issues.append(_issue(
            _ERROR, "S1_MANIFEST_MISSING",
            "manifest.safe not found in ZIP; product structure is invalid",
        ))
        return None, issues
    return candidates[0], issues


def _read_manifest_xml(zf: zipfile.ZipFile, manifest_name: str) -> tuple[ET.Element | None, list[ValidationIssue]]:
    """Read and parse manifest.safe XML.  Returns (root, []) or (None, [issue])."""
    issues: list[ValidationIssue] = []
    try:
        raw = zf.read(manifest_name)
    except Exception as exc:
        issues.append(_issue(_ERROR, "S1_MANIFEST_UNREADABLE", f"manifest.safe cannot be read: {exc}"))
        return None, issues
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        issues.append(_issue(_ERROR, "S1_MANIFEST_INVALID_XML", f"manifest.safe is not valid XML: {exc}"))
        return None, issues
    return root, issues


def _safe_dir_name(zf: zipfile.ZipFile) -> str | None:
    """Return the top-level SAFE directory name from the ZIP, or None."""
    for name in zf.namelist():
        if "/" in name:
            top = name.split("/")[0]
            if top.endswith(".SAFE") or top.endswith(".safe"):
                return top
    return None


# ---------------------------------------------------------------------------
# Manifest field extraction
# ---------------------------------------------------------------------------

def _ft(root: ET.Element, xpath: str) -> str | None:
    """Find element text, strip whitespace, return None if absent or empty."""
    el = root.find(xpath, _NS)
    if el is None or not el.text:
        return None
    text = el.text.strip()
    return text if text else None


def _parse_manifest(root: ET.Element, safe_dir: str | None) -> _ManifestData:
    data = _ManifestData()
    data.product_name = safe_dir

    data.product_type = _ft(root, ".//s1sarl1:standAloneProductInformation/s1sarl1:productType")
    data.polarisation = _ft(root, ".//s1sarl1:standAloneProductInformation/s1sarl1:polarisationChannels")

    data.sensing_start = _ft(root, ".//safe:acquisitionPeriod/safe:startTime")
    data.sensing_stop = _ft(root, ".//safe:acquisitionPeriod/safe:stopTime")

    data.platform_family = _ft(root, ".//safe:platform/safe:familyName")
    data.platform_number = _ft(root, ".//safe:platform/safe:number")

    instr_el = root.find(".//safe:platform/safe:instrument/safe:familyName", _NS)
    if instr_el is not None:
        data.instrument_family = instr_el.text.strip() if instr_el.text else None
        data.instrument_abbreviation = instr_el.get("abbreviation")

    data.sensor_mode = _ft(root, ".//s1:instrumentMode/s1:mode")

    data.orbit_number = _ft(root, ".//safe:orbitReference/safe:orbitNumber")

    data.footprint_coords = _ft(root, ".//safe:frameSet/safe:frame/safe:footPrint/gml:coordinates")

    return data


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_platform_identity(data: _ManifestData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    family = (data.platform_family or "").upper()
    if not family:
        issues.append(_issue(_ERROR, "S1_PLATFORM_MISSING", "platform familyName is absent from manifest"))
        return issues
    if family != _SENTINEL1_FAMILY:
        issues.append(_issue(
            _ERROR, "S1_PLATFORM_INVALID",
            f"platform familyName '{data.platform_family}' is not '{_SENTINEL1_FAMILY}'",
        ))
    if data.platform_number and data.platform_number.upper() not in _KNOWN_PLATFORM_NUMBERS:
        issues.append(_issue(
            _WARN, "S1_PLATFORM_NUMBER_UNKNOWN",
            f"platform number '{data.platform_number}' is not a known Sentinel-1 unit",
        ))
    return issues


def _check_instrument(data: _ManifestData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    abbrev = (data.instrument_abbreviation or "").upper()
    family = (data.instrument_family or "").upper()
    if not abbrev and not family:
        issues.append(_issue(_ERROR, "S1_INSTRUMENT_MISSING", "instrument identity is absent from manifest"))
        return issues
    if abbrev and abbrev != _SAR_ABBREVIATION:
        issues.append(_issue(
            _ERROR, "S1_INSTRUMENT_INVALID",
            f"instrument abbreviation '{data.instrument_abbreviation}' is not '{_SAR_ABBREVIATION}'",
        ))
    return issues


def _check_product_type(data: _ManifestData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    ptype = (data.product_type or "").upper()
    if not ptype:
        issues.append(_issue(_ERROR, "S1_PRODUCT_TYPE_MISSING", "productType is absent from manifest"))
        return issues
    entry = _PRODUCT_TYPE_TABLE.get(ptype)
    if entry is None:
        issues.append(_issue(
            _ERROR, "S1_PRODUCT_TYPE_UNKNOWN",
            f"productType '{data.product_type}' is not a recognised Sentinel-1 product type",
        ))
        return issues
    _valid, _hint, warn_msg = entry
    if warn_msg:
        issues.append(_issue(_WARN, "S1_PRODUCT_TYPE_USABLE_WITH_WARNINGS", warn_msg))
    return issues


def _check_sensor_mode(data: _ManifestData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    mode = (data.sensor_mode or "").upper()
    if not mode:
        issues.append(_issue(_ERROR, "S1_SENSOR_MODE_MISSING", "sensor mode is absent from manifest"))
        return issues
    entry = _MODE_TABLE.get(mode)
    if entry is None:
        issues.append(_issue(
            _WARN, "S1_SENSOR_MODE_UNRECOGNISED",
            f"sensor mode '{data.sensor_mode}' is not a standard Sentinel-1 mode",
        ))
        return issues
    _hint, warn_msg = entry
    if warn_msg:
        issues.append(_issue(_WARN, "S1_SENSOR_MODE_NON_IW", warn_msg))
    return issues


def _check_processing_level(data: _ManifestData) -> list[ValidationIssue]:
    """Derive GRDH/GRDM distinction from the product name (SAFE dir), not productType."""
    issues: list[ValidationIssue] = []
    name = data.product_name or ""
    # Product name pattern: S1A_IW_GRDH_... or S1A_IW_GRDM_...
    parts = name.replace(".SAFE", "").split("_")
    if len(parts) >= 3:
        level = parts[2].upper()
        warn_msg = _PROC_LEVEL_WARNINGS.get(level)
        if warn_msg:
            issues.append(_issue(_WARN, "S1_PROCESSING_LEVEL_SUBOPTIMAL", warn_msg))
    return issues


def _check_polarisation(data: _ManifestData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not data.polarisation or not data.polarisation.strip():
        issues.append(_issue(
            _WARN, "S1_POLARISATION_MISSING",
            "polarisation channels are absent from manifest; product interpretability may be limited",
        ))
    return issues


def _parse_sensing_time(value: str) -> datetime | None:
    """Parse manifest sensing time.  Handles both 'T' and space separators, with/without Z."""
    normalized = value.strip().replace(" ", "T")
    # manifest.safe uses microseconds without timezone: 2018-10-08T06:30:00.000000
    if not normalized.endswith("Z") and "+" not in normalized and normalized.count("-") <= 2:
        normalized += "+00:00"
    else:
        normalized = normalized.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _check_acquisition_times(data: _ManifestData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not data.sensing_start:
        issues.append(_issue(_ERROR, "S1_SENSING_START_MISSING", "sensing start time is absent from manifest"))
    if not data.sensing_stop:
        issues.append(_issue(_ERROR, "S1_SENSING_STOP_MISSING", "sensing stop time is absent from manifest"))
    if issues:
        return issues

    start_dt = _parse_sensing_time(data.sensing_start)  # type: ignore[arg-type]
    stop_dt = _parse_sensing_time(data.sensing_stop)  # type: ignore[arg-type]

    if start_dt is None:
        issues.append(_issue(
            _ERROR, "S1_SENSING_START_MALFORMED",
            f"sensing start time cannot be parsed: '{data.sensing_start}'",
        ))
    if stop_dt is None:
        issues.append(_issue(
            _ERROR, "S1_SENSING_STOP_MALFORMED",
            f"sensing stop time cannot be parsed: '{data.sensing_stop}'",
        ))
    if start_dt is not None and stop_dt is not None and stop_dt <= start_dt:
        issues.append(_issue(
            _ERROR, "S1_SENSING_TIME_ORDER_INVALID",
            "sensing stop time is not after sensing start time",
            start=data.sensing_start,
            stop=data.sensing_stop,
        ))
    return issues


def _parse_footprint(coords_raw: str) -> tuple[list[tuple[float, float]], str | None]:
    """Parse GML coordinates string (lat,lon pairs separated by spaces).

    Returns ([(lat, lon), ...], error_message_or_None).
    """
    pairs: list[tuple[float, float]] = []
    try:
        for token in coords_raw.strip().split():
            parts = token.split(",")
            if len(parts) < 2:
                return [], f"coordinate token '{token}' does not contain a comma"
            lat = float(parts[0])
            lon = float(parts[1])
            pairs.append((lat, lon))
    except ValueError as exc:
        return [], f"coordinate value cannot be parsed: {exc}"
    return pairs, None


def _check_footprint(data: _ManifestData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not data.footprint_coords:
        issues.append(_issue(
            _WARN, "S1_FOOTPRINT_MISSING",
            "spatial footprint is absent from manifest; AOI intersection cannot be performed",
        ))
        return issues

    pairs, parse_error = _parse_footprint(data.footprint_coords)
    if parse_error:
        issues.append(_issue(_ERROR, "S1_FOOTPRINT_MALFORMED", f"footprint coordinates cannot be parsed: {parse_error}"))
        return issues
    if not pairs:
        issues.append(_issue(_ERROR, "S1_FOOTPRINT_EMPTY", "footprint coordinate list is empty"))
        return issues

    for lat, lon in pairs:
        if not (-90.0 <= lat <= 90.0):
            issues.append(_issue(
                _ERROR, "S1_FOOTPRINT_LATITUDE_INVALID",
                f"footprint latitude {lat} is outside [-90, 90]",
            ))
            return issues
        if not (-180.0 <= lon <= 180.0):
            issues.append(_issue(
                _ERROR, "S1_FOOTPRINT_LONGITUDE_INVALID",
                f"footprint longitude {lon} is outside [-180, 180]",
            ))
            return issues
    return issues


def _check_orbit(data: _ManifestData) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not data.orbit_number:
        issues.append(_issue(_WARN, "S1_ORBIT_NUMBER_MISSING", "orbit number is absent from manifest"))
        return issues
    try:
        int(data.orbit_number)
    except ValueError:
        issues.append(_issue(
            _WARN, "S1_ORBIT_NUMBER_MALFORMED",
            f"orbit number '{data.orbit_number}' is not an integer",
        ))
    return issues


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify(issues: list[ValidationIssue]) -> str:
    if any(i.severity == _ERROR for i in issues):
        return _CLASS_INVALID
    if any(i.severity == _WARN for i in issues):
        return _CLASS_USABLE
    return _CLASS_PREFERRED


# ---------------------------------------------------------------------------
# Metadata assembly
# ---------------------------------------------------------------------------

def _build_metadata(data: _ManifestData, classification: str) -> dict[str, Any]:
    meta: dict[str, Any] = {"validation_classification": classification}
    if data.product_name is not None:
        meta["product_name"] = data.product_name
    if data.product_type is not None:
        meta["product_type"] = data.product_type
    if data.platform_family is not None:
        meta["platform"] = (
            data.platform_family
            if not data.platform_number
            else f"{data.platform_family}-{data.platform_number}"
        )
    if data.instrument_family is not None:
        meta["instrument"] = data.instrument_family
    if data.sensor_mode is not None:
        meta["sensor_mode"] = data.sensor_mode
    if data.polarisation is not None:
        meta["polarisation"] = data.polarisation
    if data.sensing_start is not None:
        meta["sensing_start"] = data.sensing_start
    if data.sensing_stop is not None:
        meta["sensing_stop"] = data.sensing_stop
    if data.orbit_number is not None:
        meta["orbit_number"] = data.orbit_number
    if data.footprint_coords is not None:
        meta["footprint_coords"] = data.footprint_coords
    return meta


# ---------------------------------------------------------------------------
# Public validator
# ---------------------------------------------------------------------------

class Sentinel1Validator(ScientificValidator):
    """Validates a Sentinel-1 ZIP/SAFE artifact.

    Checks performed:
    - ZIP can be opened and is not corrupt
    - manifest.safe is present and parseable
    - platform identifies as SENTINEL-1 / SAR
    - product type is recognised
    - sensor mode is recognised (IW preferred)
    - GRDH preferred over GRDM (WARNING for GRDM)
    - polarisation channels present
    - sensing start/stop parseable and ordered
    - spatial footprint coordinates in valid ranges
    - orbit number present

    Does not perform SAR pixel analysis, oil-spill detection, or
    AOI intersection.  Read-only: source artifact is never modified.
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

        # --- archive integrity ---
        zf, zip_issues = _open_zip(path)
        issues.extend(zip_issues)
        if zf is None:
            meta["validation_classification"] = _CLASS_INVALID
            return self._result(artifact, issues, meta)

        with zf:
            manifest_name, manifest_issues = _find_manifest(zf)
            issues.extend(manifest_issues)
            if manifest_name is None:
                meta["validation_classification"] = _CLASS_INVALID
                return self._result(artifact, issues, meta)

            safe_dir = _safe_dir_name(zf)

            root_el, xml_issues = _read_manifest_xml(zf, manifest_name)
            issues.extend(xml_issues)
            if root_el is None:
                meta["validation_classification"] = _CLASS_INVALID
                return self._result(artifact, issues, meta)

        # --- manifest field extraction ---
        data = _parse_manifest(root_el, safe_dir)

        # --- individual checks (all independent) ---
        issues.extend(_check_platform_identity(data))
        issues.extend(_check_instrument(data))
        issues.extend(_check_product_type(data))
        issues.extend(_check_sensor_mode(data))
        issues.extend(_check_processing_level(data))
        issues.extend(_check_polarisation(data))
        issues.extend(_check_acquisition_times(data))
        issues.extend(_check_footprint(data))
        issues.extend(_check_orbit(data))

        classification = _classify(issues)
        meta = _build_metadata(data, classification)
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
