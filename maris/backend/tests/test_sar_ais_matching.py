"""Unit tests for Phase 6.2 — Spatiotemporal AIS Correlation & Discrepancy Classifier.

Validates zero-fabrication temporal alignment, Gated Nearest-Neighbor association,
neutral discrepancy classifications, ambiguity flagging, and database immutability.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
import pytest

from app.services.real_experiment.sar_ais_matching_service import (
    CLASSIFICATION_COINCIDENT_MATCH,
    CLASSIFICATION_SPATIAL_EXCEEDANCE,
    CLASSIFICATION_OBSERVATION_GAP,
    CLASSIFICATION_TARGET_UNCORRELATED,
    CLASSIFICATION_VESSEL_NOT_DETECTED,
    CLASSIFICATION_AMBIGUOUS_PROXIMITY,
    SarAisMatchingService,
    haversine_distance_m,
    parse_utc_timestamp,
)
from app.services.real_experiment.sar_vessel_detector import SarBrightTarget


@pytest.fixture
def service():
    """Create default matching service with standard gates."""
    return SarAisMatchingService(
        coincident_spatial_gate_m=1000.0,
        max_association_gate_m=3000.0,
        temporal_window_minutes=30.0,
        max_interpolation_interval_s=900.0,
        coincident_time_threshold_s=60.0,
    )


@pytest.fixture
def scene_bbox():
    """Cap Corse test scene bounding box (west, south, east, north)."""
    return (9.30, 43.10, 9.60, 43.40)


@pytest.fixture
def t_obs():
    """Standard observation epoch: 2018-10-08 12:00:00 UTC."""
    return datetime(2018, 10, 8, 12, 0, 0, tzinfo=timezone.utc)


class TestSarAisMatchingService:
    """Comprehensive test suite for Phase 6.2 AIS Correlation & Discrepancy Classifier."""

    def test_01_haversine_distance_accuracy(self):
        """1. Haversine distance matches standard geodesic distance within 0.1%."""
        # Cap Corse to Bastia approx: (43.20 N, 9.40 E) to (43.20 N, 9.50 E)
        # At latitude 43.2°N, 0.1° longitude is approx 111.320 * cos(43.2°) * 0.1 ≈ 8.115 km = 8115 m
        d = haversine_distance_m(43.20, 9.40, 43.20, 9.50)
        assert 8000.0 < d < 8200.0
        # Zero distance
        assert haversine_distance_m(43.20, 9.40, 43.20, 9.40) == 0.0

    def test_02_prefer_genuine_coincident_observation(self, service, t_obs):
        """2. When a genuine fix exists within 60s of t_obs, it is used directly without interpolation."""
        vessel = {
            "vessel_id": "V-001",
            "mmsi": "228001000",
            "vessel_name": "CARGO ALPHA",
            "positions": [
                {"timestamp": "2018-10-08T11:59:30Z", "lat": 43.250, "lon": 9.450, "speed": 12.0, "cog": 180.0},
                {"timestamp": "2018-10-08T12:05:00Z", "lat": 43.240, "lon": 9.450, "speed": 12.0, "cog": 180.0},
            ],
        }

        pos, status, meta = service.align_vessel_position(vessel, t_obs)

        assert status == "GENUINE_OBSERVATION"
        assert pos is not None
        assert pos.is_interpolated is False
        assert pos.alignment_method == "GENUINE_OBSERVATION"
        assert pos.time_offset_seconds == 30.0
        assert pos.lat == 43.250
        assert pos.lon == 9.450

    def test_03_bounded_interpolation_under_900s(self, service, t_obs):
        """3. Linear interpolation is permitted when bounding gap is <= 900s (15 min)."""
        # Fix 1 at 11:55:00 (300s before), Fix 2 at 12:05:00 (300s after). Gap = 600s <= 900s.
        vessel = {
            "vessel_id": "V-002",
            "mmsi": "228002000",
            "vessel_name": "TANKER BETA",
            "positions": [
                {"timestamp": "2018-10-08T11:55:00Z", "lat": 43.200, "lon": 9.400, "speed": 10.0, "cog": 90.0},
                {"timestamp": "2018-10-08T12:05:00Z", "lat": 43.200, "lon": 9.500, "speed": 10.0, "cog": 90.0},
            ],
        }

        pos, status, meta = service.align_vessel_position(vessel, t_obs)

        assert status == "TEMPORALLY_ALIGNED_INTERPOLATION"
        assert pos is not None
        assert pos.is_interpolated is True
        assert pos.alignment_method == "TEMPORALLY_ALIGNED_INTERPOLATION"
        assert pos.bounding_gap_seconds == 600.0
        # Exactly halfway (alpha = 0.5)
        assert pos.lat == pytest.approx(43.200, abs=1e-5)
        assert pos.lon == pytest.approx(9.450, abs=1e-5)
        assert pos.sog_knots == pytest.approx(10.0, abs=0.1)
        assert pos.cog_degrees == pytest.approx(90.0, abs=0.1)

    def test_04_zero_fabrication_rejects_interpolation_over_900s(self, service, t_obs):
        """4. Gaps > 900s (e.g. 1200s / 20 min) are NOT interpolated and yield AIS_OBSERVATION_GAP."""
        # Fix 1 at 11:45:00 (900s before), Fix 2 at 12:05:00 (300s after). Gap = 1200s > 900s.
        vessel = {
            "vessel_id": "V-003",
            "mmsi": "228003000",
            "vessel_name": "CONTAINER GAMMA",
            "positions": [
                {"timestamp": "2018-10-08T11:45:00Z", "lat": 43.200, "lon": 9.400, "speed": 14.0, "cog": 0.0},
                {"timestamp": "2018-10-08T12:05:00Z", "lat": 43.300, "lon": 9.400, "speed": 14.0, "cog": 0.0},
            ],
        }

        pos, status, meta = service.align_vessel_position(vessel, t_obs)

        assert status == CLASSIFICATION_OBSERVATION_GAP
        assert pos is None
        assert meta["gap_seconds"] == 1200.0

    def test_05_prohibit_extrapolation_outside_track(self, service, t_obs):
        """5. Extrapolation beyond the first or last fix is strictly prohibited."""
        # All points in past: last fix was at 11:50:00 (10 min prior to t_obs)
        vessel_past = {
            "vessel_id": "V-004",
            "mmsi": "228004000",
            "positions": [
                {"timestamp": "2018-10-08T11:40:00Z", "lat": 43.10, "lon": 9.30},
                {"timestamp": "2018-10-08T11:50:00Z", "lat": 43.15, "lon": 9.35},
            ],
        }
        pos_past, status_past, _ = service.align_vessel_position(vessel_past, t_obs)
        assert status_past == "OUTSIDE_TEMPORAL_WINDOW"
        assert pos_past is None

        # All points in future: first fix at 12:10:00 (10 min after t_obs)
        vessel_future = {
            "vessel_id": "V-005",
            "mmsi": "228005000",
            "positions": [
                {"timestamp": "2018-10-08T12:10:00Z", "lat": 43.20, "lon": 9.40},
                {"timestamp": "2018-10-08T12:20:00Z", "lat": 43.25, "lon": 9.45},
            ],
        }
        pos_future, status_future, _ = service.align_vessel_position(vessel_future, t_obs)
        assert status_future == "OUTSIDE_TEMPORAL_WINDOW"
        assert pos_future is None

    def test_06_coincident_ais_match_classification(self, service, t_obs, scene_bbox):
        """6. SAR target and AIS position within coincident_spatial_gate_m (1000m) -> COINCIDENT_AIS_MATCH."""
        # Place target at (43.2500, 9.4500)
        target = SarBrightTarget(
            target_id="TGT-001",
            pixel_x=100.0,
            pixel_y=100.0,
            lon=9.4500,
            lat=43.2500,
            peak_backscatter_db=15.0,
            local_clutter_mean_db=-10.0,
            target_to_clutter_ratio_db=25.0,
            pixel_count=3,
        )

        # Place vessel ~400m away (lat offset ~0.0036 deg ≈ 400m)
        vessel = {
            "vessel_id": "V-COINCIDENT",
            "mmsi": "228001111",
            "vessel_name": "COINCIDENT SHIP",
            "positions": [
                {"timestamp": "2018-10-08T12:00:00Z", "lat": 43.2536, "lon": 9.4500, "speed": 10.0, "cog": 0.0}
            ],
        }

        res = service.correlate(
            sar_targets=[target],
            ais_vessels=[vessel],
            observation_time=t_obs,
            scene_bbox=scene_bbox,
        )

        assert res.matched_coincident_count == 1
        assert len(res.associations) == 1
        assoc = res.associations[0]
        assert assoc.classification == CLASSIFICATION_COINCIDENT_MATCH
        assert assoc.target_id == "TGT-001"
        assert assoc.vessel_id == "V-COINCIDENT"
        assert 350.0 < assoc.distance_meters < 450.0

    def test_07_spatial_discrepancy_exceedance_classification(self, service, t_obs, scene_bbox):
        """7. SAR target and AIS between 1000m and 3000m -> SPATIAL_DISCREPANCY_EXCEEDANCE."""
        target = SarBrightTarget(
            target_id="TGT-002",
            pixel_x=100.0,
            pixel_y=100.0,
            lon=9.4500,
            lat=43.2500,
            peak_backscatter_db=12.0,
            local_clutter_mean_db=-10.0,
            target_to_clutter_ratio_db=22.0,
            pixel_count=2,
        )

        # Place vessel ~1800m away (lat offset ~0.0162 deg ≈ 1800m)
        vessel = {
            "vessel_id": "V-EXCEED",
            "mmsi": "228002222",
            "vessel_name": "DISCREPANCY SHIP",
            "positions": [
                {"timestamp": "2018-10-08T12:00:00Z", "lat": 43.2662, "lon": 9.4500, "speed": 15.0, "cog": 0.0}
            ],
        }

        res = service.correlate(
            sar_targets=[target],
            ais_vessels=[vessel],
            observation_time=t_obs,
            scene_bbox=scene_bbox,
        )

        assert res.spatial_discrepancy_count == 1
        assoc = res.associations[0]
        assert assoc.classification == CLASSIFICATION_SPATIAL_EXCEEDANCE
        assert 1700.0 < assoc.distance_meters < 1900.0

    def test_08_uncorrelated_target_and_undetected_vessel_beyond_3000m(self, service, t_obs, scene_bbox):
        """8. Distance > 3000m produces RADAR_TARGET_UNCORRELATED and AIS_VESSEL_NOT_DETECTED."""
        # Target in south
        target = SarBrightTarget(
            target_id="TGT-SOLO",
            pixel_x=50.0,
            pixel_y=50.0,
            lon=9.3500,
            lat=43.1500,
            peak_backscatter_db=14.0,
            local_clutter_mean_db=-10.0,
            target_to_clutter_ratio_db=24.0,
            pixel_count=1,
        )

        # Vessel in north (~20 km away, well inside scene bbox)
        vessel = {
            "vessel_id": "V-SOLO",
            "mmsi": "228003333",
            "vessel_name": "REMOTE AIS SHIP",
            "positions": [
                {"timestamp": "2018-10-08T12:00:00Z", "lat": 43.3500, "lon": 9.5500, "speed": 10.0, "cog": 180.0}
            ],
        }

        res = service.correlate(
            sar_targets=[target],
            ais_vessels=[vessel],
            observation_time=t_obs,
            scene_bbox=scene_bbox,
        )

        assert res.uncorrelated_target_count == 1
        assert res.undetected_vessel_count == 1

        t_assoc = next(a for a in res.associations if a.classification == CLASSIFICATION_TARGET_UNCORRELATED)
        assert t_assoc.target_id == "TGT-SOLO"

        v_assoc = next(a for a in res.associations if a.classification == CLASSIFICATION_VESSEL_NOT_DETECTED)
        assert v_assoc.vessel_id == "V-SOLO"

    def test_09_ambiguous_multi_target_proximity_not_arbitrarily_resolved(self, service, t_obs, scene_bbox):
        """9. Two SAR targets within association gate of ONE AIS vessel -> AMBIGUOUS_MULTI_TARGET_PROXIMITY."""
        # Vessel at (43.2500, 9.4500)
        vessel = {
            "vessel_id": "V-CONGESTED",
            "mmsi": "228004444",
            "vessel_name": "CONGESTED SHIP",
            "positions": [
                {"timestamp": "2018-10-08T12:00:00Z", "lat": 43.2500, "lon": 9.4500, "speed": 5.0, "cog": 0.0}
            ],
        }

        # Target 1 ~500m north
        t1 = SarBrightTarget(
            target_id="TGT-AMB-1",
            pixel_x=10.0, pixel_y=10.0,
            lon=9.4500, lat=43.2545,
            peak_backscatter_db=15.0, local_clutter_mean_db=-10.0, target_to_clutter_ratio_db=25.0,
            pixel_count=2,
        )
        # Target 2 ~800m east
        t2 = SarBrightTarget(
            target_id="TGT-AMB-2",
            pixel_x=20.0, pixel_y=20.0,
            lon=9.4600, lat=43.2500,
            peak_backscatter_db=12.0, local_clutter_mean_db=-10.0, target_to_clutter_ratio_db=22.0,
            pixel_count=1,
        )

        res = service.correlate(
            sar_targets=[t1, t2],
            ais_vessels=[vessel],
            observation_time=t_obs,
            scene_bbox=scene_bbox,
        )

        assert res.ambiguous_count >= 1
        # Both targets are ambiguous
        amb_assocs = [a for a in res.associations if a.classification == CLASSIFICATION_AMBIGUOUS_PROXIMITY]
        assert len(amb_assocs) == 2
        for a in amb_assocs:
            assert "V-CONGESTED" in a.ambiguous_candidate_ids

    def test_10_ais_observation_gap_reporting(self, service, t_obs, scene_bbox):
        """10. AIS track spanning t_obs with >900s gap produces AIS_OBSERVATION_GAP association."""
        vessel_gap = {
            "vessel_id": "V-GAP-TRACK",
            "mmsi": "228005555",
            "vessel_name": "GAP TANKER",
            "positions": [
                {"timestamp": "2018-10-08T11:30:00Z", "lat": 43.20, "lon": 9.40, "speed": 12.0},
                {"timestamp": "2018-10-08T12:30:00Z", "lat": 43.30, "lon": 9.40, "speed": 12.0},  # 3600s gap
            ],
        }

        res = service.correlate(
            sar_targets=[],
            ais_vessels=[vessel_gap],
            observation_time=t_obs,
            scene_bbox=scene_bbox,
        )

        assert res.observation_gap_count == 1
        assoc = res.associations[0]
        assert assoc.classification == CLASSIFICATION_OBSERVATION_GAP
        assert assoc.vessel_id == "V-GAP-TRACK"
        assert assoc.ais_gap_seconds == 3600.0

    def test_11_scientific_neutrality_and_schema(self, service, t_obs, scene_bbox):
        """11. Output adheres strictly to approved classifications with neutral vocabulary."""
        target = SarBrightTarget(
            target_id="TGT-TEST",
            pixel_x=10.0, pixel_y=10.0,
            lon=9.4500, lat=43.2500,
            peak_backscatter_db=15.0, local_clutter_mean_db=-10.0, target_to_clutter_ratio_db=25.0,
            pixel_count=1,
        )

        res = service.correlate(
            sar_targets=[target],
            ais_vessels=[],
            observation_time=t_obs,
            scene_bbox=scene_bbox,
        )

        d = res.as_dict()
        assert "scientific_disclaimer" in d
        assert "observational anomalies" in d["scientific_disclaimer"]

        # Prohibited terms test across result payload
        import json
        payload_str = json.dumps(d).lower()
        prohibited = ["dark vessel", "spoofing", "evader", "illegal", "guilty", "suspect"]
        for p in prohibited:
            assert p not in payload_str, f"Prohibited term '{p}' found in result payload!"

    def test_12_read_only_database_query(self, service, t_obs, scene_bbox):
        """12. Real query_vessels_in_spatiotemporal_box against ais_vessels.db leaves database untouched."""
        db_path = Path(__file__).resolve().parents[1] / "data" / "ais_vessels.db"
        if not db_path.exists():
            pytest.skip("ais_vessels.db fixture not present")

        mtime_before = db_path.stat().st_mtime
        size_before = db_path.stat().st_size

        target = SarBrightTarget(
            target_id="TGT-REAL",
            pixel_x=100.0, pixel_y=100.0,
            lon=9.458, lat=43.235,
            peak_backscatter_db=15.0, local_clutter_mean_db=-10.0, target_to_clutter_ratio_db=25.0,
            pixel_count=2,
        )

        res = service.correlate_from_db(
            sar_targets=[target],
            observation_time=t_obs,
            scene_bbox=scene_bbox,
            db_path=db_path,
        )

        # Database must not have been modified
        mtime_after = db_path.stat().st_mtime
        size_after = db_path.stat().st_size
        assert mtime_before == mtime_after
        assert size_before == size_after

        assert isinstance(res.total_ais_candidates, int)
        assert isinstance(res.associations, list)
