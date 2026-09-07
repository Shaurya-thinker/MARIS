"""Offline test suite for Stage A4.5 AIS position validation."""

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.acquisition.schemas import AcquiredArtifact
from app.models.common import AssetType, Provenance
from app.validation.ais import AisValidator
from app.validation.schemas import ValidationSeverity


def _artifact(location: Path | str) -> AcquiredArtifact:
    return AcquiredArtifact(
        asset_type=AssetType.VESSEL_TRACK,
        location=str(location),
        source="ais-adapter:static",
        acquisition_time=datetime.now(timezone.utc),
        provenance=Provenance(
            product_id="maris.ais.positions.v1",
            retrieved_at=datetime.now(timezone.utc),
        ),
    )


def _valid_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "timestamp": "2024-06-01T12:00:00Z",
        "lat": 51.5,
        "lon": 2.5,
        "mmsi": "244123456",
        "imo": "9123456",
        "vessel_name": "TEST VESSEL",
        "sog": 10.5,
        "cog": 180.0,
        "heading": 182.0,
        "navigation_status": "under way using engine",
    }
    record.update(overrides)
    return record


def _valid_payload(records: list[dict[str, object]] | None = None) -> dict[str, object]:
    if records is None:
        records = [
            _valid_record(timestamp="2024-06-01T12:00:00Z", lat=51.0, lon=2.0),
            _valid_record(timestamp="2024-06-01T13:00:00Z", lat=51.5, lon=2.5),
        ]
    return {
        "schema": "maris.ais.positions.v1",
        "query": {
            "investigation_id": "test-inv",
            "time_window": {
                "start": "2024-06-01T00:00:00Z",
                "end": "2024-06-02T00:00:00Z",
            },
            "bounding_box": {"west": 1.0, "south": 50.0, "east": 3.0, "north": 53.0},
        },
        "records": records,
    }


class AisValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = AisValidator()

    def test_validator_properties(self) -> None:
        self.assertEqual(self.validator.name, "ais_validator")
        self.assertEqual(self.validator.version, "1.0.0")

    def test_valid_ais_artifact_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "ais_positions.json"
            file_path.write_text(json.dumps(_valid_payload()), encoding="utf-8")

            result = self.validator.validate(_artifact(file_path))
            self.assertTrue(result.passed)
            self.assertEqual(result.metadata["validation_classification"], "PREFERRED")
            self.assertEqual(len([i for i in result.issues if i.severity == ValidationSeverity.ERROR]), 0)
            self.assertEqual(len([i for i in result.issues if i.severity == ValidationSeverity.WARNING]), 0)

    def test_missing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "non_existent.json"
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            self.assertEqual(result.metadata["validation_classification"], "INVALID")
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_ARTIFACT_NOT_FOUND", codes)

    def test_empty_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "empty.json"
            file_path.write_bytes(b"")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            self.assertEqual(result.metadata["validation_classification"], "INVALID")
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_ARTIFACT_EMPTY", codes)

    def test_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "corrupt.json"
            file_path.write_text("{invalid json:", encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            self.assertEqual(result.metadata["validation_classification"], "INVALID")
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_JSON_INVALID", codes)

    def test_missing_positions_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "no_positions.json"
            file_path.write_text(json.dumps({"schema": "maris.ais.positions.v1"}), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            self.assertEqual(result.metadata["validation_classification"], "INVALID")
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_POSITIONS_MISSING", codes)

    def test_positions_is_not_a_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "not_list.json"
            file_path.write_text(json.dumps({"schema": "maris.ais.positions.v1", "records": "not a list"}), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            self.assertEqual(result.metadata["validation_classification"], "INVALID")
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_POSITIONS_NOT_LIST", codes)

    def test_empty_positions_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "empty_list.json"
            file_path.write_text(json.dumps({"schema": "maris.ais.positions.v1", "records": []}), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            self.assertEqual(result.metadata["validation_classification"], "INVALID")
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_POSITIONS_EMPTY", codes)

    def test_malformed_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "malformed.json"
            payload = _valid_payload([_valid_record(), "not-a-dict"])  # type: ignore[list-item]
            file_path.write_text(json.dumps(payload), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_RECORD_INVALID", codes)

    def test_missing_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "no_ts.json"
            rec = _valid_record()
            rec.pop("timestamp")
            file_path.write_text(json.dumps(_valid_payload([rec])), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_TIMESTAMP_MISSING", codes)

    def test_invalid_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "bad_ts.json"
            rec = _valid_record(timestamp="not-a-timestamp-string")
            file_path.write_text(json.dumps(_valid_payload([rec])), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_TIMESTAMP_INVALID", codes)

    def test_missing_latitude(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "no_lat.json"
            rec = _valid_record()
            rec.pop("lat")
            file_path.write_text(json.dumps(_valid_payload([rec])), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_LATITUDE_INVALID", codes)

    def test_missing_longitude(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "no_lon.json"
            rec = _valid_record()
            rec.pop("lon")
            file_path.write_text(json.dumps(_valid_payload([rec])), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_LONGITUDE_INVALID", codes)

    def test_invalid_latitude(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "bad_lat.json"
            rec = _valid_record(lat=95.0)
            file_path.write_text(json.dumps(_valid_payload([rec])), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_LATITUDE_INVALID", codes)

    def test_invalid_longitude(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "bad_lon.json"
            rec = _valid_record(lon=-190.0)
            file_path.write_text(json.dumps(_valid_payload([rec])), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_LONGITUDE_INVALID", codes)

    def test_non_finite_coordinate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "non_finite.json"
            rec = _valid_record(lat="NaN")
            file_path.write_text(json.dumps(_valid_payload([rec])), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_LATITUDE_INVALID", codes)

    def test_non_monotonic_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "non_mono.json"
            records = [
                _valid_record(timestamp="2024-06-01T12:00:00Z"),
                _valid_record(timestamp="2024-06-01T14:00:00Z"),
                _valid_record(timestamp="2024-06-01T11:00:00Z"),
            ]
            file_path.write_text(json.dumps(_valid_payload(records)), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertTrue(result.passed)
            self.assertEqual(result.metadata["validation_classification"], "USABLE_WITH_WARNINGS")
            self.assertEqual(result.metadata["temporal_order"], "non_monotonic")
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_TIMESTAMP_NONMONOTONIC", codes)


    def test_duplicate_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "dup_ts.json"
            records = [
                _valid_record(timestamp="2024-06-01T12:00:00Z", lat=51.0),
                _valid_record(timestamp="2024-06-01T12:00:00Z", lat=51.5),
            ]
            file_path.write_text(json.dumps(_valid_payload(records)), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertTrue(result.passed)
            self.assertEqual(result.metadata["validation_classification"], "USABLE_WITH_WARNINGS")
            self.assertEqual(result.metadata["duplicate_timestamp_count"], 1)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_DUPLICATE_TIMESTAMP", codes)

    def test_duplicate_complete_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "dup_pos.json"
            rec = _valid_record()
            records = [dict(rec), dict(rec)]
            file_path.write_text(json.dumps(_valid_payload(records)), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertTrue(result.passed)
            self.assertEqual(result.metadata["validation_classification"], "USABLE_WITH_WARNINGS")
            self.assertEqual(result.metadata["duplicate_position_count"], 1)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_DUPLICATE_POSITION", codes)

    def test_missing_mmsi_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "no_mmsi.json"
            rec = _valid_record()
            rec.pop("mmsi")
            file_path.write_text(json.dumps(_valid_payload([rec])), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertTrue(result.passed)
            self.assertEqual(result.metadata["validation_classification"], "USABLE_WITH_WARNINGS")
            self.assertEqual(result.metadata["records_with_mmsi"], 0)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_OPTIONAL_IDENTITY_MISSING", codes)

    def test_malformed_mmsi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "bad_mmsi.json"
            rec = _valid_record(mmsi="123")  # Expected 9 digits
            file_path.write_text(json.dumps(_valid_payload([rec])), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_MMSI_INVALID", codes)

    def test_malformed_imo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "bad_imo.json"
            rec = _valid_record(imo="INVALID_IMO")
            file_path.write_text(json.dumps(_valid_payload([rec])), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_IMO_INVALID", codes)

    def test_missing_optional_vessel_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "empty_name.json"
            rec = _valid_record(vessel_name="")
            file_path.write_text(json.dumps(_valid_payload([rec])), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertTrue(result.passed)
            self.assertEqual(result.metadata["validation_classification"], "USABLE_WITH_WARNINGS")
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_OPTIONAL_IDENTITY_MISSING", codes)

    def test_negative_sog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "bad_sog.json"
            rec = _valid_record(sog=-5.0)
            file_path.write_text(json.dumps(_valid_payload([rec])), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_SOG_INVALID", codes)

    def test_invalid_cog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "bad_cog.json"
            rec = _valid_record(cog=365.0)
            file_path.write_text(json.dumps(_valid_payload([rec])), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_COG_INVALID", codes)

    def test_invalid_heading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "bad_heading.json"
            rec = _valid_record(heading=-1.0)
            file_path.write_text(json.dumps(_valid_payload([rec])), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_HEADING_INVALID", codes)

    def test_partial_invalid_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "partial_bad.json"
            records = [
                _valid_record(timestamp="2024-06-01T12:00:00Z"),
                _valid_record(timestamp="2024-06-01T13:00:00Z", lat=120.0),  # invalid lat
            ]
            file_path.write_text(json.dumps(_valid_payload(records)), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            self.assertEqual(result.metadata["validation_classification"], "INVALID")
            self.assertEqual(result.metadata["valid_position_count"], 1)
            self.assertEqual(result.metadata["invalid_position_count"], 1)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_PARTIAL_INVALID_RECORDS", codes)

    def test_all_records_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "all_bad.json"
            records = [
                _valid_record(lat=100.0),
                _valid_record(lon=-200.0),
            ]
            file_path.write_text(json.dumps(_valid_payload(records)), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertFalse(result.passed)
            self.assertEqual(result.metadata["validation_classification"], "INVALID")
            self.assertEqual(result.metadata["valid_position_count"], 0)
            self.assertEqual(result.metadata["invalid_position_count"], 2)
            codes = [i.code for i in result.issues]
            self.assertIn("AIS_ALL_RECORDS_INVALID", codes)

    def test_metadata_population(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "metadata.json"
            records = [
                _valid_record(timestamp="2024-06-01T10:00:00Z", lat=50.0, lon=1.0, sog=5.0, cog=90.0, heading=90.0),
                _valid_record(timestamp="2024-06-01T12:00:00Z", lat=52.0, lon=3.0, sog=15.0, cog=270.0, heading=270.0),
            ]
            file_path.write_text(json.dumps(_valid_payload(records)), encoding="utf-8")
            result = self.validator.validate(_artifact(file_path))
            self.assertTrue(result.passed)

            self.assertEqual(result.metadata["artifact_schema"], "maris.ais.positions.v1")
            self.assertEqual(result.metadata["position_count"], 2)
            self.assertEqual(result.metadata["valid_position_count"], 2)
            self.assertEqual(result.metadata["invalid_position_count"], 0)

            self.assertEqual(result.metadata["latitude_min"], 50.0)
            self.assertEqual(result.metadata["latitude_max"], 52.0)
            self.assertEqual(result.metadata["longitude_min"], 1.0)
            self.assertEqual(result.metadata["longitude_max"], 3.0)

            self.assertEqual(result.metadata["sog_min"], 5.0)
            self.assertEqual(result.metadata["sog_max"], 15.0)
            self.assertEqual(result.metadata["cog_min"], 90.0)
            self.assertEqual(result.metadata["cog_max"], 270.0)
            self.assertEqual(result.metadata["heading_min"], 90.0)
            self.assertEqual(result.metadata["heading_max"], 270.0)

            self.assertEqual(result.metadata["time_count"], 2)
            self.assertEqual(result.metadata["time_start"], "2024-06-01T10:00:00+00:00")
            self.assertEqual(result.metadata["time_end"], "2024-06-01T12:00:00+00:00")
            self.assertEqual(result.metadata["temporal_span_seconds"], 7200.0)
            self.assertEqual(result.metadata["temporal_order"], "ascending")

    def test_source_artifact_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "read_only.json"
            content = json.dumps(_valid_payload())
            file_path.write_text(content, encoding="utf-8")

            mtime_before = file_path.stat().st_mtime
            result = self.validator.validate(_artifact(file_path))
            self.assertTrue(result.passed)

            self.assertEqual(file_path.read_text(encoding="utf-8"), content)
            self.assertEqual(file_path.stat().st_mtime, mtime_before)

    def test_unexpected_validation_exception_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file_path = Path(tmp) / "internal_error.json"
            file_path.write_text(json.dumps(_valid_payload()), encoding="utf-8")

            with patch("app.validation.ais._parse_timestamp", side_effect=RuntimeError("simulated internal error")):
                result = self.validator.validate(_artifact(file_path))
                self.assertFalse(result.passed)
                self.assertEqual(result.metadata["validation_classification"], "INVALID")
                codes = [i.code for i in result.issues]
                self.assertIn("AIS_INTERNAL_ERROR", codes)


if __name__ == "__main__":
    unittest.main()
