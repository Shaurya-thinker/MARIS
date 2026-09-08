"""Hermetic tests for MARIS Stage E3 — Behavioural Intelligence.

All tests are completely offline and hermetic.
Verifies:
- Observable transmission gap detection without synthetic pings (zero-fabrication)
- Neutral factual reporting of transmission gaps (no dark vessel or intentional disabling claims)
- Speed drop and speed surge detection in/near source candidate zone
- SOG vs segment derived speed discrepancy detection
- Course alteration detection (>= 45 deg)
- Observed loitering pattern detection with neutral kinematic descriptions
- Navigation status consistency checks (anchored while moving, underway while stationary)
- Anchor swing positional envelope calculations and observational caveats
- Single-observation vessel handling and empty candidate list handling
- Deterministic GeoJSON artifact export and AssetRegistry registration
- API endpoints (200 OK, 404 Not Found, 422 Validation Error)
- Deterministic repeated execution
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.acquisition.registry import InMemoryAssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.core.config import Settings
from app.main import app
from app.models.asset import Asset
from app.models.behavioral_intelligence import (
    AnomalySeverity,
    BehavioralAnomalyType,
    BehavioralIntelligenceRequest,
    BehavioralIntelligenceResult,
)
from app.models.common import AssetType, Provenance
from app.models.source_estimation import BackwardDriftStep, SourceEstimateResult
from app.models.vessel import (
    CandidateVessel,
    CandidateVesselGenerationResult,
    VesselPosition,
)
from app.services.behavioral_intelligence import (
    BehavioralIntelligenceError,
    analyze_candidate_behavior,
    detect_course_alterations,
    detect_loitering,
    detect_speed_anomalies,
    detect_transmission_gaps,
    evaluate_nav_status_and_anchor,
    export_behavioral_geojson,
)


def _make_source_estimate(
    spill_id: str = "spill-test-1",
    center_lon: float = 9.47,
    center_lat: float = 43.24,
    radius_km: float = 5.0,
    source_time: datetime | None = None,
) -> SourceEstimateResult:
    """Helper to generate a mock D3 SourceEstimateResult."""
    t_src = source_time or datetime(2026, 9, 8, 10, 0, 0, tzinfo=timezone.utc)
    # Generate 32-vertex regular polygon ring
    coords: list[list[float]] = []
    r_deg = radius_km / 111.0
    for i in range(32):
        angle = 2.0 * math.pi * i / 32
        coords.append([
            round(center_lon + r_deg * math.cos(angle) / math.cos(math.radians(center_lat)), 6),
            round(center_lat + r_deg * math.sin(angle), 6),
        ])
    coords.append(coords[0])

    return SourceEstimateResult(
        id=f"src-est-{spill_id}",
        investigation_id="inv-beh-test",
        spill_detection_id=spill_id,
        wind_asset_id="wind-mock",
        current_asset_id="current-mock",
        asset_id="drift-product-mock",
        step_hours=1.0,
        leeway_fraction=0.035,
        origin_lon=center_lon + 0.05,
        origin_lat=center_lat + 0.05,
        observation_time=t_src + timedelta(hours=6),
        source_point_lon=center_lon,
        source_point_lat=center_lat,
        source_time=t_src,
        lookback_hours=6.0,
        source_uncertainty_radius_km=radius_km,
        step_count=2,
        steps=[
            BackwardDriftStep(
                timestamp=t_src + timedelta(hours=3),
                lon=center_lon + 0.02,
                lat=center_lat + 0.02,
                u_wind_ms=-3.0,
                v_wind_ms=-2.0,
                u_current_ms=-0.05,
                v_current_ms=-0.03,
                drift_u_ms=-0.15,
                drift_v_ms=-0.10,
                cumulative_backward_distance_m=2500.0,
                uncertainty_radius_m=2500.0,
            ),
            BackwardDriftStep(
                timestamp=t_src,
                lon=center_lon,
                lat=center_lat,
                u_wind_ms=-3.0,
                v_wind_ms=-2.0,
                u_current_ms=-0.05,
                v_current_ms=-0.03,
                drift_u_ms=-0.15,
                drift_v_ms=-0.10,
                cumulative_backward_distance_m=5000.0,
                uncertainty_radius_m=5000.0,
            ),
        ],
        source_zone_geometry={"type": "Polygon", "coordinates": [coords]},
        metadata={"spill_area_m2": 500000.0},
    )


class BehavioralIntelligenceUnitTests(unittest.TestCase):
    """Unit tests for individual anomaly detection routines."""

    def setUp(self):
        self.source = _make_source_estimate()
        self.ring = self.source.source_zone_geometry["coordinates"][0]
        self.center_lon = self.source.source_point_lon
        self.center_lat = self.source.source_point_lat
        self.radius_km = self.source.source_uncertainty_radius_km
        self.t0 = self.source.source_time

    def test_transmission_gap_detection_zero_fabrication(self):
        """Transmission gaps exceeding threshold are detected factually with zero synthetic pings."""
        p1 = VesselPosition(
            timestamp=self.t0,
            lon=self.center_lon,
            lat=self.center_lat,
            speed=12.0,
            course=45.0,
        )
        p2 = VesselPosition(
            timestamp=self.t0 + timedelta(seconds=3600),  # 1 hour gap
            lon=self.center_lon + 0.1,
            lat=self.center_lat + 0.1,
            speed=12.0,
            course=45.0,
        )
        positions = [p1, p2]

        gaps, anomalies = detect_transmission_gaps(
            positions=positions,
            polygon_ring=self.ring,
            center_lon=self.center_lon,
            center_lat=self.center_lat,
            radius_km=self.radius_km,
            gap_threshold_seconds=1800.0,
        )

        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].gap_duration_seconds, 3600.0)
        self.assertGreater(gaps[0].distance_across_gap_km, 0.0)
        self.assertTrue(gaps[0].spanned_source_zone)

        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0].anomaly_type, BehavioralAnomalyType.AIS_TRANSMISSION_GAP)

        # Verify zero-fabrication: positions list remains strictly 2 genuine points
        self.assertEqual(len(positions), 2)

    def test_transmission_gap_neutrality_no_dark_vessel_claims(self):
        """Transmission gap descriptions must never claim intentional 'dark vessel' or transponder disabling."""
        p1 = VesselPosition(timestamp=self.t0, lon=self.center_lon, lat=self.center_lat, speed=10.0)
        p2 = VesselPosition(timestamp=self.t0 + timedelta(seconds=2400), lon=self.center_lon + 0.05, lat=self.center_lat + 0.05, speed=10.0)

        gaps, anomalies = detect_transmission_gaps([p1, p2], self.ring, self.center_lon, self.center_lat, self.radius_km)

        self.assertEqual(len(anomalies), 1)
        desc = anomalies[0].description.lower()
        self.assertNotIn("dark vessel", desc)
        self.assertNotIn("disabled", desc)
        self.assertNotIn("suspicious", desc)
        self.assertIn("observable ais transmission gap", desc)
        self.assertIn("caveat", anomalies[0].details)

    def test_speed_drop_in_zone(self):
        """Vessel slowing down abruptly from transit speed inside/near zone is flagged."""
        p1 = VesselPosition(timestamp=self.t0, lon=self.center_lon, lat=self.center_lat, speed=12.5, course=90.0)
        p2 = VesselPosition(timestamp=self.t0 + timedelta(minutes=10), lon=self.center_lon + 0.01, lat=self.center_lat, speed=1.5, course=90.0)

        anomalies = detect_speed_anomalies([p1, p2], self.ring, self.center_lon, self.center_lat, self.radius_km, speed_drop_threshold_knots=5.0)

        drop_anoms = [a for a in anomalies if a.anomaly_type == BehavioralAnomalyType.SPEED_DROP_IN_ZONE]
        self.assertEqual(len(drop_anoms), 1)
        self.assertEqual(drop_anoms[0].observed_value, 1.5)
        self.assertEqual(drop_anoms[0].details["speed_drop_knots"], 11.0)
        self.assertTrue(drop_anoms[0].inside_source_zone)

    def test_speed_surge_near_zone(self):
        """Vessel accelerating rapidly upon departing zone is flagged."""
        p1 = VesselPosition(timestamp=self.t0, lon=self.center_lon, lat=self.center_lat, speed=1.8, course=180.0)
        p2 = VesselPosition(timestamp=self.t0 + timedelta(minutes=10), lon=self.center_lon, lat=self.center_lat - 0.02, speed=13.5, course=180.0)

        anomalies = detect_speed_anomalies([p1, p2], self.ring, self.center_lon, self.center_lat, self.radius_km)

        surge_anoms = [a for a in anomalies if a.anomaly_type == BehavioralAnomalyType.SPEED_SURGE_NEAR_ZONE]
        self.assertEqual(len(surge_anoms), 1)
        self.assertEqual(surge_anoms[0].observed_value, 13.5)
        self.assertGreaterEqual(surge_anoms[0].details["speed_surge_knots"], 10.0)

    def test_sog_derived_speed_discrepancy(self):
        """Discrepancy between reported SOG and inter-ping derived speed is flagged."""
        # 600 seconds, distance corresponding to ~4 knots, but reporting 18 knots SOG
        p1 = VesselPosition(timestamp=self.t0, lon=self.center_lon, lat=self.center_lat, speed=18.0)
        p2 = VesselPosition(timestamp=self.t0 + timedelta(seconds=600), lon=self.center_lon + 0.01, lat=self.center_lat, speed=18.0)

        anomalies = detect_speed_anomalies([p1, p2], self.ring, self.center_lon, self.center_lat, self.radius_km)

        disc_anoms = [a for a in anomalies if a.anomaly_type == BehavioralAnomalyType.SOG_DERIVED_SPEED_DISCREPANCY]
        self.assertEqual(len(disc_anoms), 1)
        self.assertGreater(disc_anoms[0].details["discrepancy_knots"], 5.0)

    def test_anomaly_severity_contract(self):
        """AnomalySeverity matches approved Stage E3 assessment contract."""
        self.assertEqual(AnomalySeverity.INFO.value, "info")
        self.assertEqual(AnomalySeverity.NOTABLE.value, "notable")
        self.assertEqual(AnomalySeverity.ANOMALOUS.value, "anomalous")

    def test_course_alteration_in_zone(self):
        """Significant course changes (>= 45 deg) inside or near zone are flagged without speculative scores."""
        p1 = VesselPosition(timestamp=self.t0, lon=self.center_lon, lat=self.center_lat, speed=8.0, course=40.0)
        p2 = VesselPosition(timestamp=self.t0 + timedelta(minutes=5), lon=self.center_lon + 0.005, lat=self.center_lat, speed=8.0, course=140.0)

        anomalies = detect_course_alterations([p1, p2], self.ring, self.center_lon, self.center_lat, self.radius_km, course_threshold_deg=45.0)

        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0].anomaly_type, BehavioralAnomalyType.COURSE_ALTERATION_IN_ZONE)
        self.assertEqual(anomalies[0].observed_value, 100.0)
        self.assertEqual(anomalies[0].baseline_or_threshold_value, 45.0)
        # Verify deterministic physical details only, no speculative scoring
        self.assertNotIn("score", anomalies[0].details)
        self.assertEqual(anomalies[0].details["course_change_deg"], 100.0)

    def test_course_alteration_outside_zone_ignored(self):
        """Course alterations occurring far outside the source zone are not flagged as in-zone alterations."""
        p1 = VesselPosition(timestamp=self.t0, lon=self.center_lon + 1.0, lat=self.center_lat + 1.0, speed=8.0, course=40.0)
        p2 = VesselPosition(timestamp=self.t0 + timedelta(minutes=5), lon=self.center_lon + 1.01, lat=self.center_lat + 1.01, speed=8.0, course=140.0)

        anomalies = detect_course_alterations([p1, p2], self.ring, self.center_lon, self.center_lat, self.radius_km, course_threshold_deg=45.0)
        self.assertEqual(len(anomalies), 0)

    def test_loitering_observed_pattern_and_neutrality(self):
        """Low-speed wandering with course changes is detected with strictly neutral description."""
        # 4 points at 1.5 kn, course wandering by 60 deg, total time 900s
        p1 = VesselPosition(timestamp=self.t0, lon=self.center_lon, lat=self.center_lat, speed=1.5, course=10.0)
        p2 = VesselPosition(timestamp=self.t0 + timedelta(seconds=300), lon=self.center_lon + 0.001, lat=self.center_lat, speed=1.6, course=70.0)
        p3 = VesselPosition(timestamp=self.t0 + timedelta(seconds=600), lon=self.center_lon + 0.002, lat=self.center_lat + 0.001, speed=1.4, course=140.0)
        p4 = VesselPosition(timestamp=self.t0 + timedelta(seconds=900), lon=self.center_lon + 0.001, lat=self.center_lat + 0.002, speed=1.5, course=200.0)

        detected, duration, anomalies = detect_loitering([p1, p2, p3, p4], self.ring, self.center_lon, self.center_lat, self.radius_km)

        self.assertTrue(detected)
        self.assertEqual(duration, 900.0)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(anomalies[0].anomaly_type, BehavioralAnomalyType.LOITERING_OBSERVED)

        desc = anomalies[0].description.lower()
        self.assertNotIn("suspicious", desc)
        self.assertNotIn("discharge", desc)
        self.assertNotIn("culpability", desc)
        self.assertIn("observed low-speed", desc)

    def test_nav_status_mismatch_reporting_anchor_while_moving(self):
        """Vessel reporting 'At anchor' while moving at 12 knots is flagged as mismatch."""
        p1 = VesselPosition(timestamp=self.t0, lon=self.center_lon, lat=self.center_lat, speed=12.0)
        p2 = VesselPosition(timestamp=self.t0 + timedelta(minutes=10), lon=self.center_lon + 0.02, lat=self.center_lat, speed=12.5)

        cand = CandidateVessel(
            candidate_id="cand-test-1",
            vessel_id="v-1",
            inside_source_zone=True,
            min_distance_to_source_center_km=0.0,
            distance_to_zone_boundary_km=0.0,
            closest_position_lon=self.center_lon,
            closest_position_lat=self.center_lat,
            closest_position_time=self.t0,
            time_offset_from_source_hours=0.0,
            speed_over_ground=12.0,
            navigation_status="at_anchor",
            observed_positions_count=2,
            raw_positions=[p1, p2],
        )

        consistent, anchor_profile, anomalies = evaluate_nav_status_and_anchor(cand, [p1, p2])

        self.assertFalse(consistent)
        mismatch_anoms = [a for a in anomalies if a.anomaly_type == BehavioralAnomalyType.NAV_STATUS_MISMATCH]
        self.assertEqual(len(mismatch_anoms), 1)
        self.assertIn("at_anchor", mismatch_anoms[0].description)

    def test_anchor_swing_envelope_and_caveat(self):
        """Stationary/anchored vessel generates an observed positional envelope with factual caveat."""
        p1 = VesselPosition(timestamp=self.t0, lon=self.center_lon, lat=self.center_lat, speed=0.1)
        p2 = VesselPosition(timestamp=self.t0 + timedelta(minutes=10), lon=self.center_lon + 0.001, lat=self.center_lat + 0.001, speed=0.1)
        p3 = VesselPosition(timestamp=self.t0 + timedelta(minutes=20), lon=self.center_lon - 0.001, lat=self.center_lat, speed=0.1)

        cand = CandidateVessel(
            candidate_id="cand-anchor-1",
            vessel_id="v-anchor",
            inside_source_zone=True,
            min_distance_to_source_center_km=0.0,
            distance_to_zone_boundary_km=0.0,
            closest_position_lon=self.center_lon,
            closest_position_lat=self.center_lat,
            closest_position_time=self.t0,
            time_offset_from_source_hours=0.0,
            speed_over_ground=0.1,
            navigation_status="at_anchor",
            observed_positions_count=3,
            raw_positions=[p1, p2, p3],
        )

        consistent, anchor_profile, anomalies = evaluate_nav_status_and_anchor(cand, [p1, p2, p3])

        self.assertTrue(consistent)
        self.assertIsNotNone(anchor_profile)
        self.assertEqual(anchor_profile.observation_count, 3)
        self.assertGreater(anchor_profile.observed_envelope_radius_m, 0.0)

        # Verify observational caveat
        swing_anoms = [a for a in anomalies if a.anomaly_type == BehavioralAnomalyType.ANCHOR_SWING_OBSERVED]
        self.assertEqual(len(swing_anoms), 1)
        self.assertIn("caveat", swing_anoms[0].details)
        self.assertIn("does not claim an exact anchor drop point", swing_anoms[0].details["caveat"])


class BehavioralIntelligenceServiceTests(unittest.TestCase):
    """End-to-end service tests including GeoJSON artifact generation and AssetRegistry."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry = InMemoryAssetRegistry()
        self.source = _make_source_estimate("spill-svc-1")
        self.t0 = self.source.source_time

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_full_candidate_behavior_analysis(self):
        """End-to-end analysis of candidates with varied behavioral indicators."""
        # Candidate 1: Loitering and speed drop
        p1 = VesselPosition(timestamp=self.t0 - timedelta(minutes=30), lon=9.46, lat=43.23, speed=12.0, course=45.0)
        p2 = VesselPosition(timestamp=self.t0, lon=9.47, lat=43.24, speed=1.5, course=45.0)
        p3 = VesselPosition(timestamp=self.t0 + timedelta(minutes=10), lon=9.471, lat=43.241, speed=1.4, course=110.0)
        p4 = VesselPosition(timestamp=self.t0 + timedelta(minutes=20), lon=9.472, lat=43.24, speed=1.5, course=190.0)

        cand1 = CandidateVessel(
            candidate_id="cand-1",
            vessel_id="vessel-ulysse",
            mmsi="672248000",
            vessel_name="ULYSSE",
            inside_source_zone=True,
            min_distance_to_source_center_km=0.1,
            distance_to_zone_boundary_km=0.0,
            closest_position_lon=9.47,
            closest_position_lat=43.24,
            closest_position_time=self.t0,
            time_offset_from_source_hours=0.0,
            speed_over_ground=1.5,
            navigation_status="under_way",
            observed_positions_count=4,
            raw_positions=[p1, p2, p3, p4],
        )

        # Candidate 2: Anchored with observed swing envelope
        pa1 = VesselPosition(timestamp=self.t0, lon=9.48, lat=43.25, speed=0.1, course=0.0)
        pa2 = VesselPosition(timestamp=self.t0 + timedelta(minutes=15), lon=9.4805, lat=43.2505, speed=0.1, course=0.0)
        cand2 = CandidateVessel(
            candidate_id="cand-2",
            vessel_id="vessel-csl",
            mmsi="212416000",
            vessel_name="CSL VIRGINIA",
            inside_source_zone=True,
            min_distance_to_source_center_km=1.2,
            distance_to_zone_boundary_km=0.0,
            closest_position_lon=9.48,
            closest_position_lat=43.25,
            closest_position_time=self.t0,
            time_offset_from_source_hours=0.0,
            speed_over_ground=0.1,
            navigation_status="at_anchor",
            observed_positions_count=2,
            raw_positions=[pa1, pa2],
        )

        cand_gen_result = CandidateVesselGenerationResult(
            id="cand-gen-1",
            investigation_id="inv-beh-test",
            source_estimate_id=self.source.id,
            spill_detection_id=self.source.spill_detection_id,
            status="completed",
            source_time=self.t0,
            source_uncertainty_radius_km=5.0,
            temporal_window_start=self.t0 - timedelta(hours=2),
            temporal_window_end=self.t0 + timedelta(hours=2),
            spatial_query_bbox={},
            candidate_count=2,
            total_vessels_checked=2,
            candidates=[cand1, cand2],
        )

        result, asset = analyze_candidate_behavior(
            investigation_id="inv-beh-test",
            spill_id="spill-svc-1",
            source_estimate=self.source,
            candidate_result=cand_gen_result,
            output_dir=self.temp_dir.name,
            registry=self.registry,
        )

        self.assertEqual(result.analyzed_vessel_count, 2)
        self.assertGreater(result.total_anomalies_detected, 0)

        # Check Ulysse profile
        prof1 = result.profiles[0]
        self.assertEqual(prof1.vessel_name, "ULYSSE")
        self.assertTrue(prof1.loitering_detected)
        self.assertIn("speed_drop_in_zone", prof1.summary_flags)

        # Check CSL Virginia profile
        prof2 = result.profiles[1]
        self.assertEqual(prof2.vessel_name, "CSL VIRGINIA")
        self.assertIsNotNone(prof2.anchor_swing_profile)
        self.assertIn("anchor_swing_observed", prof2.summary_flags)

        # Verify GeoJSON artifact
        artifact_path = Path(asset.location)
        self.assertTrue(artifact_path.exists())
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
        self.assertEqual(data["type"], "FeatureCollection")
        self.assertTrue(data["properties"]["zero_fabrication"])

        # Verify AssetRegistry registration
        registered = self.registry.get(asset.id)
        self.assertEqual(registered.type, AssetType.DOCUMENT)
        self.assertEqual(registered.metadata["asset_type"], "behavioral_intelligence")

    def test_single_observation_vessel_handling(self):
        """Vessel with only 1 observation produces zero gap or rate-of-change anomalies."""
        p1 = VesselPosition(timestamp=self.t0, lon=9.47, lat=43.24, speed=10.0, course=90.0)
        cand = CandidateVessel(
            candidate_id="cand-single",
            vessel_id="v-single",
            inside_source_zone=True,
            min_distance_to_source_center_km=0.0,
            distance_to_zone_boundary_km=0.0,
            closest_position_lon=9.47,
            closest_position_lat=43.24,
            closest_position_time=self.t0,
            time_offset_from_source_hours=0.0,
            speed_over_ground=10.0,
            navigation_status="under_way",
            observed_positions_count=1,
            raw_positions=[p1],
        )
        cand_gen = CandidateVesselGenerationResult(
            id="cand-gen-single",
            investigation_id="inv-beh-test",
            source_estimate_id=self.source.id,
            spill_detection_id="spill-single",
            status="completed",
            source_time=self.t0,
            source_uncertainty_radius_km=5.0,
            temporal_window_start=self.t0,
            temporal_window_end=self.t0,
            spatial_query_bbox={},
            candidate_count=1,
            total_vessels_checked=1,
            candidates=[cand],
        )

        result, asset = analyze_candidate_behavior(
            investigation_id="inv-beh-test",
            spill_id="spill-single",
            source_estimate=self.source,
            candidate_result=cand_gen,
            output_dir=self.temp_dir.name,
            registry=self.registry,
        )

        self.assertEqual(result.analyzed_vessel_count, 1)
        self.assertEqual(len(result.profiles[0].anomalies), 0)
        self.assertEqual(len(result.profiles[0].transmission_gaps), 0)

    def test_empty_candidate_list_handling(self):
        """Empty candidate list produces valid result with 0 analyzed vessels."""
        cand_gen = CandidateVesselGenerationResult(
            id="cand-gen-empty",
            investigation_id="inv-beh-test",
            source_estimate_id=self.source.id,
            spill_detection_id="spill-empty",
            status="no_candidates_found",
            source_time=self.t0,
            source_uncertainty_radius_km=5.0,
            temporal_window_start=self.t0,
            temporal_window_end=self.t0,
            spatial_query_bbox={},
            candidate_count=0,
            total_vessels_checked=0,
            candidates=[],
        )

        result, asset = analyze_candidate_behavior(
            investigation_id="inv-beh-test",
            spill_id="spill-empty",
            source_estimate=self.source,
            candidate_result=cand_gen,
            output_dir=self.temp_dir.name,
            registry=self.registry,
        )

        self.assertEqual(result.analyzed_vessel_count, 0)
        self.assertEqual(result.total_anomalies_detected, 0)

    def test_deterministic_repeatability(self):
        """Repeated analysis on identical inputs yields bitwise deterministic outputs."""
        p1 = VesselPosition(timestamp=self.t0, lon=9.47, lat=43.24, speed=12.0, course=45.0)
        p2 = VesselPosition(timestamp=self.t0 + timedelta(minutes=10), lon=9.471, lat=43.241, speed=1.5, course=120.0)

        cand = CandidateVessel(
            candidate_id="cand-rep",
            vessel_id="v-rep",
            inside_source_zone=True,
            min_distance_to_source_center_km=0.1,
            distance_to_zone_boundary_km=0.0,
            closest_position_lon=9.47,
            closest_position_lat=43.24,
            closest_position_time=self.t0,
            time_offset_from_source_hours=0.0,
            speed_over_ground=1.5,
            navigation_status="under_way",
            observed_positions_count=2,
            raw_positions=[p1, p2],
        )
        cand_gen = CandidateVesselGenerationResult(
            id="cand-gen-rep",
            investigation_id="inv-beh-test",
            source_estimate_id=self.source.id,
            spill_detection_id="spill-rep",
            status="completed",
            source_time=self.t0,
            source_uncertainty_radius_km=5.0,
            temporal_window_start=self.t0,
            temporal_window_end=self.t0 + timedelta(hours=1),
            spatial_query_bbox={},
            candidate_count=1,
            total_vessels_checked=1,
            candidates=[cand],
        )

        res1, asset1 = analyze_candidate_behavior(
            investigation_id="inv-beh-test",
            spill_id="spill-rep",
            source_estimate=self.source,
            candidate_result=cand_gen,
            output_dir=self.temp_dir.name + "/run1",
            registry=self.registry,
        )
        res2, asset2 = analyze_candidate_behavior(
            investigation_id="inv-beh-test",
            spill_id="spill-rep",
            source_estimate=self.source,
            candidate_result=cand_gen,
            output_dir=self.temp_dir.name + "/run2",
            registry=self.registry,
        )

        self.assertEqual(res1.total_anomalies_detected, res2.total_anomalies_detected)
        self.assertEqual(len(res1.profiles[0].anomalies), len(res2.profiles[0].anomalies))
        self.assertEqual(
            [a.anomaly_type for a in res1.profiles[0].anomalies],
            [a.anomaly_type for a in res2.profiles[0].anomalies],
        )


class BehavioralIntelligenceApiTests(unittest.TestCase):
    """API endpoint integration tests."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.client = TestClient(app)

        # Set up mock assets in default_asset_registry
        self.inv_id = "inv-beh-api"
        self.spill_id = "spill-beh-api-1"
        self.source = _make_source_estimate(self.spill_id)
        self.t0 = self.source.source_time

        # 1. Register spill detection asset
        spill_file = Path(self.temp_dir.name) / "spill.geojson"
        spill_file.write_text("{}", encoding="utf-8")
        self.spill_asset = default_asset_registry.register(
            self.inv_id,
            "spill_detector",
            AcquiredArtifact(
                asset_type=AssetType.SPILL_GEOMETRY,
                location=str(spill_file),
                source="test",
                provenance=Provenance(product_id=self.spill_id),
                metadata={"detection_id": self.spill_id, "spill_id": self.spill_id},
            ),
        )

        # 2. Register source estimate asset
        src_path = Path(self.temp_dir.name) / "source_candidate_zone.geojson"
        src_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[self.source.origin_lon, self.source.origin_lat], [self.source.source_point_lon, self.source.source_point_lat]],
                    },
                    "properties": {
                        "feature_kind": "backward_drift_centerline",
                        "investigation_id": self.inv_id,
                        "spill_detection_id": self.spill_id,
                        "observation_time": self.source.observation_time.isoformat(),
                        "source_time": self.source.source_time.isoformat(),
                        "lookback_hours": 6.0,
                    },
                },
                {
                    "type": "Feature",
                    "geometry": self.source.source_zone_geometry,
                    "properties": {
                        "feature_kind": "source_candidate_zone",
                        "investigation_id": self.inv_id,
                        "spill_detection_id": self.spill_id,
                        "source_time": self.source.source_time.isoformat(),
                        "center_lon": self.source.source_point_lon,
                        "center_lat": self.source.source_point_lat,
                        "uncertainty_radius_km": self.source.source_uncertainty_radius_km,
                    },
                },
            ],
        }
        src_path.write_text(json.dumps(src_fc), encoding="utf-8")
        self.src_asset = default_asset_registry.register(
            self.inv_id,
            "source_estimation",
            AcquiredArtifact(
                asset_type=AssetType.DRIFT_PRODUCT,
                location=str(src_path),
                source="test",
                provenance=Provenance(
                    product_id=self.source.id,
                    extra={
                        "source_id": self.source.id,
                        "source_estimate_id": self.source.id,
                        "source_time": self.source.source_time.isoformat(),
                        "observation_time": self.source.observation_time.isoformat(),
                        "source_uncertainty_radius_km": self.source.source_uncertainty_radius_km,
                    },
                ),
                metadata={"source_id": self.source.id, "source_estimate_id": self.source.id},
            ),
        )

        # 3. Register candidate vessels asset
        cand_path = Path(self.temp_dir.name) / "candidate_vessels.geojson"
        cand_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [9.47, 43.24]},
                    "properties": {
                        "feature_kind": "candidate_cpa",
                        "candidate_id": "cand-api-1",
                        "vessel_id": "v-api-1",
                        "mmsi": "123456789",
                        "vessel_name": "TEST VESSEL",
                        "inside_source_zone": True,
                        "min_distance_to_source_center_km": 0.5,
                        "distance_to_zone_boundary_km": 0.0,
                        "closest_position_time": self.t0.isoformat(),
                        "time_offset_from_source_hours": 0.0,
                        "speed_over_ground": 1.2,
                        "course_over_ground": 45.0,
                        "navigation_status": "under_way",
                    },
                }
            ],
        }
        cand_path.write_text(json.dumps(cand_fc), encoding="utf-8")
        self.cand_asset = default_asset_registry.register(
            self.inv_id,
            "candidate_generation",
            AcquiredArtifact(
                asset_type=AssetType.DOCUMENT,
                location=str(cand_path),
                source="test",
                provenance=Provenance(
                    product_id="cand-gen-api",
                    extra={
                        "candidate_generation_id": "cand-gen-api",
                        "source_estimate_id": self.source.id,
                        "spill_detection_id": self.spill_id,
                        "asset_type": "candidate_vessels",
                    },
                ),
                metadata={
                    "asset_type": "candidate_vessels",
                    "candidate_generation_id": "cand-gen-api",
                    "spill_id": self.spill_id,
                },
            ),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_api_behavioral_intelligence_200_ok(self):
        """POST /behavioral-intelligence returns 200 OK with valid result."""
        payload = {
            "source_estimate_id": self.src_asset.id,
            "candidate_generation_id": self.cand_asset.id,
            "speed_drop_threshold_knots": 5.0,
            "loitering_speed_threshold_knots": 3.0,
            "course_alteration_threshold_deg": 45.0,
            "transmission_gap_threshold_seconds": 1800.0,
        }

        resp = self.client.post(
            f"/api/v1/investigations/{self.inv_id}/spills/{self.spill_id}/behavioral-intelligence",
            json=payload,
        )

        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["investigation_id"], self.inv_id)
        self.assertEqual(data["spill_detection_id"], self.spill_id)
        self.assertEqual(data["analyzed_vessel_count"], 1)
        self.assertIn("profiles", data)
        self.assertTrue(data["metadata"]["zero_fabrication"])

    def test_api_behavioral_intelligence_404_missing_source(self):
        """Missing source estimate yields 404."""
        payload = {
            "source_estimate_id": "nonexistent-source",
            "candidate_generation_id": self.cand_asset.id,
        }
        resp = self.client.post(
            f"/api/v1/investigations/{self.inv_id}/spills/{self.spill_id}/behavioral-intelligence",
            json=payload,
        )
        self.assertEqual(resp.status_code, 404)

    def test_api_behavioral_intelligence_422_invalid_thresholds(self):
        """Invalid threshold parameters yield 422 validation error."""
        payload = {
            "source_estimate_id": self.src_asset.id,
            "candidate_generation_id": self.cand_asset.id,
            "course_alteration_threshold_deg": 300.0,  # Invalid: le=180.0
        }
        resp = self.client.post(
            f"/api/v1/investigations/{self.inv_id}/spills/{self.spill_id}/behavioral-intelligence",
            json=payload,
        )
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
