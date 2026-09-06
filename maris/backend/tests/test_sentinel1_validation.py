"""Tests for Stage A4.2 — Sentinel-1 Product Validation.

All tests are offline. No CDSE credentials, no network access.
Minimal ZIP/SAFE fixtures are built in-memory using stdlib zipfile.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from app.acquisition.schemas import AcquiredArtifact
from app.models.common import AssetType, Provenance
from app.validation import Sentinel1Validator, ValidationSeverity
from app.validation.sentinel1 import (
    VALIDATOR_NAME,
    VALIDATOR_VERSION,
    _CLASS_INVALID,
    _CLASS_PREFERRED,
    _CLASS_USABLE,
    _check_acquisition_times,
    _check_footprint,
    _check_orbit,
    _check_platform_identity,
    _check_polarisation,
    _check_product_type,
    _check_sensor_mode,
    _ManifestData,
)

_ERROR = ValidationSeverity.ERROR
_WARN = ValidationSeverity.WARNING
_INFO = ValidationSeverity.INFO

# ---------------------------------------------------------------------------
# Manifest XML templates
# ---------------------------------------------------------------------------

_MANIFEST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<xfdu:XFDU xmlns:xfdu="urn:ccsds:schema:xfdu:1"
  xmlns:safe="http://www.esa.int/safe/sentinel/1.1"
  xmlns:s1="http://www.esa.int/safe/sentinel-1.0"
  xmlns:s1sarl1="http://www.esa.int/safe/sentinel-1.0/sentinel-1/sar/level-1"
  xmlns:gml="http://www.opengis.net/gml">
  <metadataSection>
    <metadataObject ID="generalProductInformation"><metadataWrap><xmlData>
      <s1sarl1:standAloneProductInformation>
        <s1sarl1:productType>{product_type}</s1sarl1:productType>
        <s1sarl1:polarisationChannels>{polarisation}</s1sarl1:polarisationChannels>
      </s1sarl1:standAloneProductInformation>
    </xmlData></metadataWrap></metadataObject>
    <metadataObject ID="acquisitionPeriod"><metadataWrap><xmlData>
      <safe:acquisitionPeriod>
        <safe:startTime>{start_time}</safe:startTime>
        <safe:stopTime>{stop_time}</safe:stopTime>
      </safe:acquisitionPeriod>
    </xmlData></metadataWrap></metadataObject>
    <metadataObject ID="platform"><metadataWrap><xmlData>
      <safe:platform>
        <safe:familyName>{platform_family}</safe:familyName>
        <safe:number>{platform_number}</safe:number>
        <safe:instrument>
          <safe:familyName abbreviation="{instrument_abbrev}">{instrument_family}</safe:familyName>
          <safe:extension>
            <s1:instrumentMode><s1:mode>{sensor_mode}</s1:mode></s1:instrumentMode>
          </safe:extension>
        </safe:instrument>
      </safe:platform>
    </xmlData></metadataWrap></metadataObject>
    <metadataObject ID="measurementOrbitReference"><metadataWrap><xmlData>
      <safe:orbitReference>
        <safe:orbitNumber type="start">{orbit_number}</safe:orbitNumber>
      </safe:orbitReference>
    </xmlData></metadataWrap></metadataObject>
    <metadataObject ID="measurementFrameSet"><metadataWrap><xmlData>
      <safe:frameSet><safe:frame>
        <safe:footPrint srsName="http://www.opengis.net/gml/srs/epsg.xml#4326">
          <gml:coordinates>{footprint}</gml:coordinates>
        </safe:footPrint>
      </safe:frame></safe:frameSet>
    </xmlData></metadataWrap></metadataObject>
  </metadataSection>
</xfdu:XFDU>"""

_DEFAULT_MANIFEST_FIELDS = dict(
    product_type="GRD",
    polarisation="VV VH",
    start_time="2018-10-08T06:30:00.000000",
    stop_time="2018-10-08T06:30:25.000000",
    platform_family="SENTINEL-1",
    platform_number="A",
    instrument_abbrev="SAR",
    instrument_family="C-SAR",
    sensor_mode="IW",
    orbit_number="23855",
    footprint="41.5,8.2 41.8,9.1 42.5,8.9 42.2,8.0 41.5,8.2",
)

# Canonical SAFE directory name for a GRDH product
_SAFE_DIR_GRDH = "S1A_IW_GRDH_1SDV_20181008T063000_20181008T063025_023855_029B3D_1234.SAFE"
_SAFE_DIR_GRDM = "S1A_IW_GRDM_1SDV_20181008T063000_20181008T063025_023855_029B3D_5678.SAFE"
_SAFE_DIR_SLC  = "S1A_IW_SLC__1SDV_20181008T063000_20181008T063025_023855_029B3D_9ABC.SAFE"
_SAFE_DIR_EW   = "S1A_EW_GRDM_1SDH_20181008T063000_20181008T063025_023855_029B3D_DEF0.SAFE"


# ---------------------------------------------------------------------------
# ZIP fixture builders
# ---------------------------------------------------------------------------

def _make_zip(
    safe_dir: str = _SAFE_DIR_GRDH,
    manifest_xml: str | None = None,
    include_manifest: bool = True,
    extra_entries: dict[str, bytes] | None = None,
    **manifest_overrides: str,
) -> bytes:
    """Build a minimal in-memory Sentinel-1 ZIP fixture."""
    buf = io.BytesIO()
    fields = {**_DEFAULT_MANIFEST_FIELDS, **manifest_overrides}
    xml = manifest_xml if manifest_xml is not None else _MANIFEST_TEMPLATE.format(**fields)
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        # A real SAFE product has measurement/ and annotation/ subdirs; we add
        # a placeholder so the SAFE dir appears in the namelist.
        zf.writestr(f"{safe_dir}/", "")
        if include_manifest:
            zf.writestr(f"{safe_dir}/manifest.safe", xml)
        if extra_entries:
            for name, data in extra_entries.items():
                zf.writestr(name, data)
    return buf.getvalue()


def _write_zip(tmp_dir: str, filename: str, data: bytes) -> Path:
    path = Path(tmp_dir) / filename
    path.write_bytes(data)
    return path


def _artifact(location: str, product_name: str = _SAFE_DIR_GRDH) -> AcquiredArtifact:
    return AcquiredArtifact(
        asset_type=AssetType.SATELLITE_SCENE,
        location=location,
        source="copernicus-dataspace-odata",
        provenance=Provenance(
            product_id="test-product-id",
            extra={"product_name": product_name},
        ),
    )


def _validator() -> Sentinel1Validator:
    return Sentinel1Validator()


def _codes(result) -> list[str]:
    return [i.code for i in result.issues]


def _severities(result) -> list[ValidationSeverity]:
    return [i.severity for i in result.issues]


# ---------------------------------------------------------------------------
# Validator interface
# ---------------------------------------------------------------------------

class Sentinel1ValidatorInterfaceTests(unittest.TestCase):
    def test_name(self) -> None:
        self.assertEqual(_validator().name, VALIDATOR_NAME)

    def test_version(self) -> None:
        self.assertEqual(_validator().version, VALIDATOR_VERSION)

    def test_is_scientific_validator(self) -> None:
        from app.validation import ScientificValidator
        self.assertIsInstance(_validator(), ScientificValidator)

    def test_exported_from_package(self) -> None:
        from app.validation import Sentinel1Validator as V
        self.assertIs(V, Sentinel1Validator)


# ---------------------------------------------------------------------------
# Archive integrity
# ---------------------------------------------------------------------------

class ArchiveIntegrityTests(unittest.TestCase):
    def test_valid_zip_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "product.zip", _make_zip())
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)
            self.assertEqual(result.metadata.get("validation_classification"), _CLASS_PREFERRED)

    def test_missing_file_fails(self) -> None:
        result = _validator().validate(_artifact("/nonexistent/product.zip"))
        self.assertFalse(result.passed)
        self.assertIn("S1_ARTIFACT_NOT_FOUND", _codes(result))

    def test_empty_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.zip"
            path.write_bytes(b"")
            result = _validator().validate(_artifact(str(path)))
            self.assertFalse(result.passed)
            self.assertIn("S1_ARTIFACT_EMPTY", _codes(result))

    def test_corrupt_zip_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corrupt.zip"
            path.write_bytes(b"PK\x03\x04this is not a real zip")
            result = _validator().validate(_artifact(str(path)))
            self.assertFalse(result.passed)
            self.assertIn("S1_ZIP_CORRUPT", _codes(result))

    def test_missing_manifest_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "no_manifest.zip", _make_zip(include_manifest=False))
            result = _validator().validate(_artifact(str(path)))
            self.assertFalse(result.passed)
            self.assertIn("S1_MANIFEST_MISSING", _codes(result))

    def test_invalid_xml_manifest_fails(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(f"{_SAFE_DIR_GRDH}/", "")
            zf.writestr(f"{_SAFE_DIR_GRDH}/manifest.safe", b"<<not xml>>")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad_xml.zip"
            path.write_bytes(buf.getvalue())
            result = _validator().validate(_artifact(str(path)))
            self.assertFalse(result.passed)
            self.assertIn("S1_MANIFEST_INVALID_XML", _codes(result))

    def test_source_artifact_unchanged_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = _make_zip()
            path = _write_zip(tmp, "product.zip", data)
            mtime_before = path.stat().st_mtime
            size_before = path.stat().st_size
            _validator().validate(_artifact(str(path)))
            self.assertEqual(path.stat().st_mtime, mtime_before)
            self.assertEqual(path.stat().st_size, size_before)
            self.assertEqual(path.read_bytes(), data)


# ---------------------------------------------------------------------------
# Platform / instrument identity
# ---------------------------------------------------------------------------

class PlatformIdentityTests(unittest.TestCase):
    def test_sentinel1a_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(platform_family="SENTINEL-1", platform_number="A"))
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)
            self.assertIn("SENTINEL-1-A", result.metadata.get("platform", ""))

    def test_sentinel1b_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(platform_family="SENTINEL-1", platform_number="B"))
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)

    def test_wrong_platform_family_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(platform_family="SENTINEL-2"))
            result = _validator().validate(_artifact(str(path)))
            self.assertFalse(result.passed)
            self.assertIn("S1_PLATFORM_INVALID", _codes(result))

    def test_unknown_platform_number_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(platform_number="Z"))
            result = _validator().validate(_artifact(str(path)))
            self.assertIn("S1_PLATFORM_NUMBER_UNKNOWN", _codes(result))
            warn_issues = [i for i in result.issues if i.code == "S1_PLATFORM_NUMBER_UNKNOWN"]
            self.assertEqual(warn_issues[0].severity, _WARN)

    def test_non_sar_instrument_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(instrument_abbrev="MSI"))
            result = _validator().validate(_artifact(str(path)))
            self.assertFalse(result.passed)
            self.assertIn("S1_INSTRUMENT_INVALID", _codes(result))


# ---------------------------------------------------------------------------
# Product type
# ---------------------------------------------------------------------------

class ProductTypeTests(unittest.TestCase):
    def test_grd_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(product_type="GRD"))
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)
            self.assertNotIn("S1_PRODUCT_TYPE_USABLE_WITH_WARNINGS", _codes(result))

    def test_slc_usable_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(
                safe_dir=_SAFE_DIR_SLC, product_type="SLC",
            ))
            result = _validator().validate(_artifact(str(path), _SAFE_DIR_SLC))
            self.assertTrue(result.passed)
            self.assertIn("S1_PRODUCT_TYPE_USABLE_WITH_WARNINGS", _codes(result))
            warn = [i for i in result.issues if i.code == "S1_PRODUCT_TYPE_USABLE_WITH_WARNINGS"][0]
            self.assertEqual(warn.severity, _WARN)

    def test_unknown_product_type_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(product_type="XYZ"))
            result = _validator().validate(_artifact(str(path)))
            self.assertFalse(result.passed)
            self.assertIn("S1_PRODUCT_TYPE_UNKNOWN", _codes(result))


# ---------------------------------------------------------------------------
# Sensor mode
# ---------------------------------------------------------------------------

class SensorModeTests(unittest.TestCase):
    def test_iw_preferred_no_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(sensor_mode="IW"))
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)
            self.assertNotIn("S1_SENSOR_MODE_NON_IW", _codes(result))

    def test_ew_mode_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(
                safe_dir=_SAFE_DIR_EW, sensor_mode="EW",
            ))
            result = _validator().validate(_artifact(str(path), _SAFE_DIR_EW))
            self.assertTrue(result.passed)
            self.assertIn("S1_SENSOR_MODE_NON_IW", _codes(result))

    def test_sm_mode_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(sensor_mode="SM"))
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)
            self.assertIn("S1_SENSOR_MODE_NON_IW", _codes(result))

    def test_unrecognised_mode_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(sensor_mode="XX"))
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)
            self.assertIn("S1_SENSOR_MODE_UNRECOGNISED", _codes(result))


# ---------------------------------------------------------------------------
# Processing level (GRDH vs GRDM from product name)
# ---------------------------------------------------------------------------

class ProcessingLevelTests(unittest.TestCase):
    def test_grdh_no_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(safe_dir=_SAFE_DIR_GRDH))
            result = _validator().validate(_artifact(str(path), _SAFE_DIR_GRDH))
            self.assertTrue(result.passed)
            self.assertNotIn("S1_PROCESSING_LEVEL_SUBOPTIMAL", _codes(result))

    def test_grdm_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(safe_dir=_SAFE_DIR_GRDM))
            result = _validator().validate(_artifact(str(path), _SAFE_DIR_GRDM))
            self.assertTrue(result.passed)
            self.assertIn("S1_PROCESSING_LEVEL_SUBOPTIMAL", _codes(result))
            w = [i for i in result.issues if i.code == "S1_PROCESSING_LEVEL_SUBOPTIMAL"][0]
            self.assertEqual(w.severity, _WARN)


# ---------------------------------------------------------------------------
# IW SLC usable with warning
# ---------------------------------------------------------------------------

class IwSlcTests(unittest.TestCase):
    def test_iw_slc_usable_with_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(
                safe_dir=_SAFE_DIR_SLC, product_type="SLC", sensor_mode="IW",
            ))
            result = _validator().validate(_artifact(str(path), _SAFE_DIR_SLC))
            self.assertTrue(result.passed)
            self.assertEqual(result.metadata.get("validation_classification"), _CLASS_USABLE)
            self.assertIn("S1_PRODUCT_TYPE_USABLE_WITH_WARNINGS", _codes(result))


# ---------------------------------------------------------------------------
# Polarisation
# ---------------------------------------------------------------------------

class PolarisationTests(unittest.TestCase):
    def test_vv_vh_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(polarisation="VV VH"))
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)
            self.assertNotIn("S1_POLARISATION_MISSING", _codes(result))
            self.assertEqual(result.metadata.get("polarisation"), "VV VH")

    def test_hh_hv_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(polarisation="HH HV"))
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)

    def test_missing_polarisation_warns(self) -> None:
        # Build manifest without the polarisationChannels element
        xml = _MANIFEST_TEMPLATE.replace(
            "<s1sarl1:polarisationChannels>{polarisation}</s1sarl1:polarisationChannels>\n",
            "",
        ).format(**_DEFAULT_MANIFEST_FIELDS)
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(manifest_xml=xml))
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)
            self.assertIn("S1_POLARISATION_MISSING", _codes(result))
            w = [i for i in result.issues if i.code == "S1_POLARISATION_MISSING"][0]
            self.assertEqual(w.severity, _WARN)


# ---------------------------------------------------------------------------
# Acquisition times
# ---------------------------------------------------------------------------

class AcquisitionTimeTests(unittest.TestCase):
    def test_valid_times_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(
                start_time="2018-10-08T06:30:00.000000",
                stop_time="2018-10-08T06:30:25.000000",
            ))
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)
            self.assertNotIn("S1_SENSING_START_MISSING", _codes(result))
            self.assertNotIn("S1_SENSING_STOP_MISSING", _codes(result))
            self.assertEqual(result.metadata.get("sensing_start"), "2018-10-08T06:30:00.000000")

    def test_stop_before_start_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(
                start_time="2018-10-08T06:30:25.000000",
                stop_time="2018-10-08T06:30:00.000000",
            ))
            result = _validator().validate(_artifact(str(path)))
            self.assertFalse(result.passed)
            self.assertIn("S1_SENSING_TIME_ORDER_INVALID", _codes(result))

    def test_equal_start_stop_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(
                start_time="2018-10-08T06:30:00.000000",
                stop_time="2018-10-08T06:30:00.000000",
            ))
            result = _validator().validate(_artifact(str(path)))
            self.assertFalse(result.passed)
            self.assertIn("S1_SENSING_TIME_ORDER_INVALID", _codes(result))

    def test_malformed_start_time_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(start_time="not-a-date"))
            result = _validator().validate(_artifact(str(path)))
            self.assertFalse(result.passed)
            self.assertIn("S1_SENSING_START_MALFORMED", _codes(result))

    def test_malformed_stop_time_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(stop_time="not-a-date"))
            result = _validator().validate(_artifact(str(path)))
            self.assertFalse(result.passed)
            self.assertIn("S1_SENSING_STOP_MALFORMED", _codes(result))


# ---------------------------------------------------------------------------
# Spatial footprint
# ---------------------------------------------------------------------------

class FootprintTests(unittest.TestCase):
    def test_valid_footprint_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(
                footprint="41.5,8.2 41.8,9.1 42.5,8.9 42.2,8.0 41.5,8.2",
            ))
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)
            self.assertNotIn("S1_FOOTPRINT_MISSING", _codes(result))

    def test_invalid_latitude_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(footprint="95.0,8.2 41.8,9.1"))
            result = _validator().validate(_artifact(str(path)))
            self.assertFalse(result.passed)
            self.assertIn("S1_FOOTPRINT_LATITUDE_INVALID", _codes(result))

    def test_invalid_longitude_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(footprint="41.5,200.0 41.8,9.1"))
            result = _validator().validate(_artifact(str(path)))
            self.assertFalse(result.passed)
            self.assertIn("S1_FOOTPRINT_LONGITUDE_INVALID", _codes(result))

    def test_malformed_footprint_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(footprint="not-coords"))
            result = _validator().validate(_artifact(str(path)))
            self.assertFalse(result.passed)
            self.assertIn("S1_FOOTPRINT_MALFORMED", _codes(result))

    def test_missing_footprint_warns(self) -> None:
        xml = _MANIFEST_TEMPLATE.replace(
            "      <safe:frameSet><safe:frame>\n"
            "        <safe:footPrint srsName=\"http://www.opengis.net/gml/srs/epsg.xml#4326\">\n"
            "          <gml:coordinates>{footprint}</gml:coordinates>\n"
            "        </safe:footPrint>\n"
            "      </safe:frame></safe:frameSet>\n",
            "",
        ).format(**_DEFAULT_MANIFEST_FIELDS)
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(manifest_xml=xml))
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)
            self.assertIn("S1_FOOTPRINT_MISSING", _codes(result))
            w = [i for i in result.issues if i.code == "S1_FOOTPRINT_MISSING"][0]
            self.assertEqual(w.severity, _WARN)


# ---------------------------------------------------------------------------
# Orbit
# ---------------------------------------------------------------------------

class OrbitTests(unittest.TestCase):
    def test_valid_orbit_number_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(orbit_number="23855"))
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)
            self.assertEqual(result.metadata.get("orbit_number"), "23855")

    def test_missing_orbit_warns(self) -> None:
        xml = _MANIFEST_TEMPLATE.replace(
            "      <safe:orbitReference>\n"
            "        <safe:orbitNumber type=\"start\">{orbit_number}</safe:orbitNumber>\n"
            "      </safe:orbitReference>\n",
            "",
        ).format(**_DEFAULT_MANIFEST_FIELDS)
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(manifest_xml=xml))
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)
            self.assertIn("S1_ORBIT_NUMBER_MISSING", _codes(result))
            w = [i for i in result.issues if i.code == "S1_ORBIT_NUMBER_MISSING"][0]
            self.assertEqual(w.severity, _WARN)

    def test_malformed_orbit_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(orbit_number="not-an-int"))
            result = _validator().validate(_artifact(str(path)))
            self.assertTrue(result.passed)
            self.assertIn("S1_ORBIT_NUMBER_MALFORMED", _codes(result))


# ---------------------------------------------------------------------------
# Validation classification
# ---------------------------------------------------------------------------

class ClassificationTests(unittest.TestCase):
    def test_preferred_classification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(safe_dir=_SAFE_DIR_GRDH))
            result = _validator().validate(_artifact(str(path), _SAFE_DIR_GRDH))
            self.assertTrue(result.passed)
            self.assertEqual(result.metadata.get("validation_classification"), _CLASS_PREFERRED)

    def test_usable_classification_for_grdm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(safe_dir=_SAFE_DIR_GRDM))
            result = _validator().validate(_artifact(str(path), _SAFE_DIR_GRDM))
            self.assertTrue(result.passed)
            self.assertEqual(result.metadata.get("validation_classification"), _CLASS_USABLE)

    def test_usable_classification_for_slc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(
                safe_dir=_SAFE_DIR_SLC, product_type="SLC",
            ))
            result = _validator().validate(_artifact(str(path), _SAFE_DIR_SLC))
            self.assertTrue(result.passed)
            self.assertEqual(result.metadata.get("validation_classification"), _CLASS_USABLE)

    def test_invalid_classification_on_error(self) -> None:
        result = _validator().validate(_artifact("/nonexistent/product.zip"))
        self.assertFalse(result.passed)
        self.assertEqual(result.metadata.get("validation_classification"), _CLASS_INVALID)

    def test_error_causes_passed_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(platform_family="SENTINEL-2"))
            result = _validator().validate(_artifact(str(path)))
            self.assertFalse(result.passed)

    def test_warning_keeps_passed_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(safe_dir=_SAFE_DIR_GRDM))
            result = _validator().validate(_artifact(str(path), _SAFE_DIR_GRDM))
            self.assertTrue(result.passed)
            self.assertTrue(any(i.severity == _WARN for i in result.issues))


# ---------------------------------------------------------------------------
# Validator metadata population
# ---------------------------------------------------------------------------

class MetadataTests(unittest.TestCase):
    def test_metadata_populated_on_valid_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip(safe_dir=_SAFE_DIR_GRDH))
            result = _validator().validate(_artifact(str(path), _SAFE_DIR_GRDH))
            m = result.metadata
            self.assertIn("validation_classification", m)
            self.assertIn("product_type", m)
            self.assertIn("platform", m)
            self.assertIn("instrument", m)
            self.assertIn("sensor_mode", m)
            self.assertIn("polarisation", m)
            self.assertIn("sensing_start", m)
            self.assertIn("sensing_stop", m)
            self.assertIn("orbit_number", m)
            self.assertIn("footprint_coords", m)

    def test_validator_name_and_version_in_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip())
            result = _validator().validate(_artifact(str(path)))
            self.assertEqual(result.validator_name, VALIDATOR_NAME)
            self.assertEqual(result.validator_version, VALIDATOR_VERSION)

    def test_validated_at_is_utc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip())
            before = datetime.now(timezone.utc)
            result = _validator().validate(_artifact(str(path)))
            after = datetime.now(timezone.utc)
            self.assertGreaterEqual(result.validated_at, before)
            self.assertLessEqual(result.validated_at, after)

    def test_no_credentials_in_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_zip(tmp, "p.zip", _make_zip())
            result = _validator().validate(_artifact(str(path)))
            dumped = str(result.model_dump())
            for word in ("password", "api_key", "access_token", "secret"):
                self.assertNotIn(word, dumped.lower())


# ---------------------------------------------------------------------------
# Unit-level checks on _ManifestData helpers
# ---------------------------------------------------------------------------

class ManifestDataUnitTests(unittest.TestCase):
    def _data(self, **overrides) -> _ManifestData:
        d = _ManifestData()
        d.product_type = "GRD"
        d.platform_family = "SENTINEL-1"
        d.platform_number = "A"
        d.instrument_family = "C-SAR"
        d.instrument_abbreviation = "SAR"
        d.sensor_mode = "IW"
        d.polarisation = "VV VH"
        d.sensing_start = "2018-10-08T06:30:00.000000"
        d.sensing_stop = "2018-10-08T06:30:25.000000"
        d.orbit_number = "23855"
        d.footprint_coords = "41.5,8.2 41.8,9.1 42.5,8.9 42.2,8.0 41.5,8.2"
        d.product_name = _SAFE_DIR_GRDH
        for k, v in overrides.items():
            setattr(d, k, v)
        return d

    def test_platform_missing_errors(self) -> None:
        d = self._data(platform_family=None)
        issues = _check_platform_identity(d)
        codes = [i.code for i in issues]
        self.assertIn("S1_PLATFORM_MISSING", codes)
        self.assertTrue(all(i.severity == _ERROR for i in issues if i.code == "S1_PLATFORM_MISSING"))

    def test_product_type_missing_errors(self) -> None:
        d = self._data(product_type=None)
        issues = _check_product_type(d)
        self.assertTrue(any(i.code == "S1_PRODUCT_TYPE_MISSING" for i in issues))

    def test_sensor_mode_missing_errors(self) -> None:
        d = self._data(sensor_mode=None)
        issues = _check_sensor_mode(d)
        self.assertTrue(any(i.code == "S1_SENSOR_MODE_MISSING" for i in issues))

    def test_polarisation_missing_warns(self) -> None:
        d = self._data(polarisation=None)
        issues = _check_polarisation(d)
        self.assertTrue(any(i.code == "S1_POLARISATION_MISSING" and i.severity == _WARN for i in issues))

    def test_orbit_missing_warns(self) -> None:
        d = self._data(orbit_number=None)
        issues = _check_orbit(d)
        self.assertTrue(any(i.code == "S1_ORBIT_NUMBER_MISSING" and i.severity == _WARN for i in issues))

    def test_footprint_bad_lat_errors(self) -> None:
        d = self._data(footprint_coords="95.0,8.2 41.8,9.1")
        issues = _check_footprint(d)
        self.assertTrue(any(i.code == "S1_FOOTPRINT_LATITUDE_INVALID" and i.severity == _ERROR for i in issues))

    def test_footprint_bad_lon_errors(self) -> None:
        d = self._data(footprint_coords="41.5,200.0 41.8,9.1")
        issues = _check_footprint(d)
        self.assertTrue(any(i.code == "S1_FOOTPRINT_LONGITUDE_INVALID" and i.severity == _ERROR for i in issues))

    def test_time_order_invalid_errors(self) -> None:
        d = self._data(
            sensing_start="2018-10-08T06:30:25.000000",
            sensing_stop="2018-10-08T06:30:00.000000",
        )
        issues = _check_acquisition_times(d)
        self.assertTrue(any(i.code == "S1_SENSING_TIME_ORDER_INVALID" and i.severity == _ERROR for i in issues))


if __name__ == "__main__":
    unittest.main()
