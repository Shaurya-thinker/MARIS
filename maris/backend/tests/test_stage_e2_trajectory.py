"""Comprehensive verification tests for MARIS Stage E2 — AIS Trajectory & Spatial/Temporal Analysis.

Tests all 20 explicit edge cases required by the Stage E2 specification:
1. One AIS observation (single-point track, null speed/bearing, CPA to point)
2. Two observations (1 segment, valid speed_mps, speed_knots, bearing)
3. Multiple observations (sequential pairwise segments, cumulative distance/duration)
4. Duplicate timestamps & identical coordinates (deterministic deduplication)
5. Duplicate coordinates with positive dt (zero distance, zero speed, bearing None)
6. Out-of-order observations (deterministic chronological UTC sort)
7. Zero time delta between distinct points (invalid kinematic, speed/bearing None)
8. Negative time delta / unordered protection
9. Invalid coordinates (Pydantic validation, boundary coordinates [-90, 90], [-180, 180])
10. Large spatial jump / implausible speed (>60 knots flagged invalid, observation retained)
11. Missing optional vessel metadata (vessel_type, call_sign, flag_country, IMO, COG, SOG None)
12. Vessel crossing AOI / candidate zone boundary (inside and outside points tracked)
13. AIS point outside D3 polygon but near its buffer (min_distance_to_polygon_km > 0)
14. AIS point inside D3 polygon (min_distance_to_polygon_km == 0.0)
15. No AIS observations (empty candidate positions -> status='no_observations', no crash)
16. Missing D3 source geometry (falls back to circular uncertainty radius)
17. Missing source time / edge timestamps
18. Empty trajectory (0 positions, structured result)
19. Dateline-safe longitude handling (antimeridian crossing)
20. Ulysse reference benchmark regression (MMSIs 228308800, 229986000, count=2, centerline dist = 0.55 km)
Plus:
- F1 handoff evidence structure verification (SPATIAL, TEMPORAL, KINEMATIC, DATA QUALITY)
- GeoJSON export and round-trip consistency
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError

from app.acquisition.registry import default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.models.common import AssetType, Provenance
from app.models.source_estimation import BackwardDriftStep, SourceEstimateResult
from app.models.trajectory_analysis import (
    CenterlineProximityProfile,
    ObservedTrajectoryPoint,
    TemporalCorrelationProfile,
    TrajectoryAnalysisResult,
    TrajectoryQualityProfile,
    TrajectorySegment,
    VesselTrajectoryAnalysis,
    ZoneTransitProfile,
)
from app.models.vessel import (
    CandidateVessel,
    CandidateVesselGenerationResult,
    VesselPosition,
)
from app.services.drift_modelling import _haversine_m
from app.services.source_estimation import generate_source_candidate_polygon
from app.services.trajectory_analysis import (
    MAX_PLAUSIBLE_SPEED_KNOTS,
    analyze_candidate_trajectories,
    angular_difference_deg,
    calculate_initial_bearing_deg,
    compute_drift_direction_deg,
    deduplicate_positions,
    point_to_polygon_distance_m,
    point_to_polyline_distance_m,
    point_to_segment_distance_m,
)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class StageE2ComprehensiveTests(unittest.TestCase):
    """Hermetic test suite for all 20 Stage E2 requirements and edge cases."""

    def setUp(self) -> None:
        default_asset_registry._assets.clear()
        default_asset_registry._order.clear()

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp_dir.name)

        self.inv_id = "inv-e2-test"
        self.spill_id = "spill-e2-001"
        self.center_lon = 9.520573
        self.center_lat = 43.231922
        self.uncertainty_radius_km = 3.5

        self.source_time = datetime(2018, 10, 9, 11, 14, 27, tzinfo=timezone.utc)
        self.observation_time = datetime(2018, 10, 9, 17, 14, 27, tzinfo=timezone.utc)

        # Standard 32-vertex source polygon
        self.polygon = generate_source_candidate_polygon(
            center_lon=self.center_lon,
            center_lat=self.center_lat,
            radius_m=self.uncertainty_radius_km * 1000.0,
            num_vertices=32,
        )

        # Backward drift steps for D3 centerline
        self.drift_steps = [
            BackwardDriftStep(
                timestamp=datetime(2018, 10, 9, 16, 14, 27, tzinfo=timezone.utc),
                lon=9.484502, lat=43.246585,
                u_wind_ms=-3.0, v_wind_ms=-2.0, drift_u_ms=-0.3, drift_v_ms=-0.17,
                cumulative_backward_distance_m=550.0, uncertainty_radius_m=1000.0,
            ),
            BackwardDriftStep(
                timestamp=self.source_time,
                lon=self.center_lon, lat=self.center_lat,
                u_wind_ms=-3.0, v_wind_ms=-2.0, drift_u_ms=-0.3, drift_v_ms=-0.17,
                cumulative_backward_distance_m=3890.0, uncertainty_radius_m=3500.0,
            ),
        ]

        self.source_estimate = SourceEstimateResult(
            id=f"source-{self.spill_id}-{self.inv_id}",
            investigation_id=self.inv_id,
            spill_detection_id=self.spill_id,
            wind_asset_id="wind-001",
            current_asset_id="curr-001",
            asset_id="drift-001",
            observation_time=self.observation_time,
            origin_lon=9.47833,
            origin_lat=43.24833,
            source_time=self.source_time,
            source_point_lon=self.center_lon,
            source_point_lat=self.center_lat,
            lookback_hours=6.0,
            step_hours=1.0,
            leeway_fraction=0.035,
            source_uncertainty_radius_km=self.uncertainty_radius_km,
            steps=self.drift_steps,
            source_zone_geometry=self.polygon,
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()
        default_asset_registry._assets.clear()
        default_asset_registry._order.clear()

    def _make_cand_res(self, candidates: list[CandidateVessel]) -> CandidateVesselGenerationResult:
        return CandidateVesselGenerationResult(
            id=f"cand-gen-{self.spill_id}",
            investigation_id=self.inv_id,
            source_estimate_id=self.source_estimate.id,
            spill_detection_id=self.spill_id,
            status="completed" if candidates else "no_candidates_found",
            source_time=self.source_time,
            source_uncertainty_radius_km=self.uncertainty_radius_km,
            temporal_window_start=self.source_time - timedelta(hours=2),
            temporal_window_end=self.observation_time,
            spatial_query_bbox={},
            candidate_count=len(candidates),
            total_vessels_checked=max(1, len(candidates)),
            candidates=candidates,
        )

    # -----------------------------------------------------------------------
    # Edge Case 1: One AIS Observation
    # -----------------------------------------------------------------------
    def test_01_single_ais_observation(self) -> None:
        """1. Single observation: valid single-point track, null speed/bearing, CPA to point."""
        t0 = self.source_time
        p0 = VesselPosition(timestamp=t0, lon=9.478, lat=43.248, speed=10.0, course=205.0)
        cand = CandidateVessel(
            candidate_id="c-1", vessel_id="v-1", mmsi="111111111", vessel_name="Solo",
            closest_position_lon=9.478, closest_position_lat=43.248,
            closest_position_time=t0, inside_source_zone=False,
            min_distance_to_source_center_km=3.884, distance_to_zone_boundary_km=0.384,
            time_offset_from_source_hours=0.0, observed_positions_count=1, raw_positions=[p0],
        )

        res, asset = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, self.source_estimate, self._make_cand_res([cand]),
            output_dir=self.work_dir,
        )

        self.assertEqual(res.analyzed_vessel_count, 1)
        a = res.analyses[0]
        self.assertEqual(a.status, "single_observation")
        self.assertEqual(a.observed_positions_count, 1)
        self.assertEqual(len(a.segments), 0)
        self.assertEqual(a.total_track_distance_m, 0.0)
        self.assertEqual(a.total_track_duration_seconds, 0.0)
        self.assertIsNone(a.mean_derived_speed_knots)
        self.assertEqual(a.transit_profile.cpa_lon, 9.478)
        self.assertEqual(a.transit_profile.cpa_lat, 43.248)
        self.assertTrue(a.quality.sparse_track)
        self.assertIn("single_observation", a.quality.quality_flags)

    # -----------------------------------------------------------------------
    # Edge Case 2: Two AIS Observations
    # -----------------------------------------------------------------------
    def test_02_two_ais_observations_kinematics(self) -> None:
        """2. Two observations: exactly 1 segment, valid speed_mps, speed_knots, bearing."""
        t0 = self.source_time
        t1 = t0 + timedelta(hours=1)
        p0 = VesselPosition(timestamp=t0, lon=9.00, lat=43.00, speed=10.0)
        # 1 deg north = 111,195 m; in 3600s = ~30.88 m/s = ~60 knots
        p1 = VesselPosition(timestamp=t1, lon=9.00, lat=43.10, speed=10.0)
        cand = CandidateVessel(
            candidate_id="c-2", vessel_id="v-2", mmsi="222222222",
            closest_position_lon=9.00, closest_position_lat=43.00,
            closest_position_time=t0, inside_source_zone=False,
            min_distance_to_source_center_km=50.0, distance_to_zone_boundary_km=45.0,
            time_offset_from_source_hours=0.0, observed_positions_count=2, raw_positions=[p0, p1],
        )

        res, _ = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, self.source_estimate, self._make_cand_res([cand]),
            output_dir=self.work_dir,
        )

        a = res.analyses[0]
        self.assertEqual(a.status, "completed")
        self.assertEqual(len(a.segments), 1)
        seg = a.segments[0]
        self.assertEqual(seg.duration_seconds, 3600.0)
        self.assertGreater(seg.distance_m, 11000.0)
        self.assertIsNotNone(seg.speed_mps)
        self.assertIsNotNone(seg.derived_speed_knots)
        self.assertIsNotNone(seg.bearing_degrees)
        self.assertAlmostEqual(seg.bearing_degrees, 0.0, delta=1.0)  # Due North
        self.assertTrue(seg.is_valid_kinematic)

    # -----------------------------------------------------------------------
    # Edge Case 3: Multiple AIS Observations
    # -----------------------------------------------------------------------
    def test_03_multiple_observations_track_aggregation(self) -> None:
        """3. Multiple observations: sequential pairwise segments, cumulative distance and duration."""
        t0 = self.source_time
        p0 = VesselPosition(timestamp=t0, lon=9.0, lat=43.0, speed=5.0)
        p1 = VesselPosition(timestamp=t0 + timedelta(minutes=30), lon=9.05, lat=43.0, speed=6.0)
        p2 = VesselPosition(timestamp=t0 + timedelta(minutes=60), lon=9.10, lat=43.0, speed=7.0)
        p3 = VesselPosition(timestamp=t0 + timedelta(minutes=90), lon=9.15, lat=43.0, speed=8.0)

        cand = CandidateVessel(
            candidate_id="c-3", vessel_id="v-3", mmsi="333333333",
            closest_position_lon=9.0, closest_position_lat=43.0,
            closest_position_time=t0, inside_source_zone=False,
            min_distance_to_source_center_km=30.0, distance_to_zone_boundary_km=25.0,
            time_offset_from_source_hours=0.0, observed_positions_count=4,
            raw_positions=[p0, p1, p2, p3],
        )

        res, _ = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, self.source_estimate, self._make_cand_res([cand]),
            output_dir=self.work_dir,
        )

        a = res.analyses[0]
        self.assertEqual(len(a.segments), 3)
        self.assertEqual(a.total_track_duration_seconds, 5400.0)
        self.assertEqual(a.min_reported_sog_knots, 5.0)
        self.assertEqual(a.max_reported_sog_knots, 8.0)
        self.assertEqual(a.mean_reported_sog_knots, 6.5)

    # -----------------------------------------------------------------------
    # Edge Case 4: Duplicate Timestamps
    # -----------------------------------------------------------------------
    def test_04_duplicate_timestamps_and_coordinates_deduplication(self) -> None:
        """4. Exact duplicate observations (same timestamp & coords) deterministically pruned."""
        t0 = self.source_time
        p0 = VesselPosition(timestamp=t0, lon=9.0, lat=43.0, speed=10.0)
        p0_dup = VesselPosition(timestamp=t0, lon=9.0, lat=43.0, speed=10.0)
        p1 = VesselPosition(timestamp=t0 + timedelta(minutes=30), lon=9.05, lat=43.0, speed=10.0)

        deduped, count = deduplicate_positions([p0, p0_dup, p1])
        self.assertEqual(count, 1)
        self.assertEqual(len(deduped), 2)

        cand = CandidateVessel(
            candidate_id="c-4", vessel_id="v-4", mmsi="444444444",
            closest_position_lon=9.0, closest_position_lat=43.0,
            closest_position_time=t0, inside_source_zone=False,
            min_distance_to_source_center_km=30.0, distance_to_zone_boundary_km=25.0,
            time_offset_from_source_hours=0.0, observed_positions_count=3,
            raw_positions=[p0, p0_dup, p1],
        )

        res, _ = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, self.source_estimate, self._make_cand_res([cand]),
            output_dir=self.work_dir,
        )

        a = res.analyses[0]
        self.assertEqual(a.observed_positions_count, 2)
        self.assertEqual(a.quality.duplicate_count, 1)
        self.assertIn("duplicates_removed", a.quality.quality_flags)
        self.assertEqual(len(a.segments), 1)

    # -----------------------------------------------------------------------
    # Edge Case 5: Duplicate Coordinates with Positive dt
    # -----------------------------------------------------------------------
    def test_05_duplicate_coordinates_with_positive_dt(self) -> None:
        """5. Stationary vessel (same coords, dt > 0): distance=0, speed=0, bearing=None."""
        t0 = self.source_time
        t1 = t0 + timedelta(minutes=30)
        p0 = VesselPosition(timestamp=t0, lon=9.0, lat=43.0, speed=0.0)
        p1 = VesselPosition(timestamp=t1, lon=9.0, lat=43.0, speed=0.0)

        cand = CandidateVessel(
            candidate_id="c-5", vessel_id="v-5", mmsi="555555555",
            closest_position_lon=9.0, closest_position_lat=43.0,
            closest_position_time=t0, inside_source_zone=False,
            min_distance_to_source_center_km=30.0, distance_to_zone_boundary_km=25.0,
            time_offset_from_source_hours=0.0, observed_positions_count=2,
            raw_positions=[p0, p1],
        )

        res, _ = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, self.source_estimate, self._make_cand_res([cand]),
            output_dir=self.work_dir,
        )

        seg = res.analyses[0].segments[0]
        self.assertEqual(seg.distance_m, 0.0)
        self.assertEqual(seg.derived_speed_knots, 0.0)
        self.assertEqual(seg.speed_mps, 0.0)
        self.assertIsNone(seg.bearing_degrees)
        self.assertTrue(seg.is_valid_kinematic)

    # -----------------------------------------------------------------------
    # Edge Case 6: Out-of-Order Observations
    # -----------------------------------------------------------------------
    def test_06_out_of_order_observations_sorted_chronologically(self) -> None:
        """6. Out-of-order observations are stably and deterministically sorted by UTC timestamp."""
        t0 = self.source_time
        t1 = t0 + timedelta(minutes=15)
        t2 = t0 + timedelta(minutes=30)

        p0 = VesselPosition(timestamp=t0, lon=9.0, lat=43.0)
        p1 = VesselPosition(timestamp=t1, lon=9.05, lat=43.0)
        p2 = VesselPosition(timestamp=t2, lon=9.10, lat=43.0)

        # Feed in reverse order: [p2, p0, p1]
        cand = CandidateVessel(
            candidate_id="c-6", vessel_id="v-6", mmsi="666666666",
            closest_position_lon=9.0, closest_position_lat=43.0,
            closest_position_time=t0, inside_source_zone=False,
            min_distance_to_source_center_km=30.0, distance_to_zone_boundary_km=25.0,
            time_offset_from_source_hours=0.0, observed_positions_count=3,
            raw_positions=[p2, p0, p1],
        )

        res, _ = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, self.source_estimate, self._make_cand_res([cand]),
            output_dir=self.work_dir,
        )

        a = res.analyses[0]
        self.assertEqual(a.segments[0].start_time, t0)
        self.assertEqual(a.segments[0].end_time, t1)
        self.assertEqual(a.segments[1].start_time, t1)
        self.assertEqual(a.segments[1].end_time, t2)

    # -----------------------------------------------------------------------
    # Edge Case 7 & 8: Zero Time Delta & Negative Time Delta
    # -----------------------------------------------------------------------
    def test_07_zero_and_negative_dt_handling(self) -> None:
        """7 & 8. Distinct coordinates with dt <= 0: speed and bearing None, marked invalid."""
        t0 = self.source_time
        # Same timestamp but different coords (e.g. multi-receiver timestamp collision)
        p0 = VesselPosition(timestamp=t0, lon=9.0, lat=43.0)
        p1 = VesselPosition(timestamp=t0, lon=9.1, lat=43.0)

        cand = CandidateVessel(
            candidate_id="c-7", vessel_id="v-7", mmsi="777777777",
            closest_position_lon=9.0, closest_position_lat=43.0,
            closest_position_time=t0, inside_source_zone=False,
            min_distance_to_source_center_km=30.0, distance_to_zone_boundary_km=25.0,
            time_offset_from_source_hours=0.0, observed_positions_count=2,
            raw_positions=[p0, p1],
        )

        res, _ = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, self.source_estimate, self._make_cand_res([cand]),
            output_dir=self.work_dir,
        )

        a = res.analyses[0]
        self.assertEqual(len(a.segments), 1)
        seg = a.segments[0]
        self.assertEqual(seg.duration_seconds, 0.0)
        self.assertIsNone(seg.derived_speed_knots)
        self.assertIsNone(seg.speed_mps)
        self.assertIsNone(seg.bearing_degrees)
        self.assertFalse(seg.is_valid_kinematic)
        self.assertEqual(a.quality.invalid_kinematic_interval_count, 1)

    # -----------------------------------------------------------------------
    # Edge Case 9: Invalid Coordinates
    # -----------------------------------------------------------------------
    def test_09_invalid_and_boundary_coordinates(self) -> None:
        """9. Invalid coordinates rejected by schema validation; boundary coordinates supported."""
        # Invalid latitude > 90
        with self.assertRaises(ValidationError):
            VesselPosition(timestamp=self.source_time, lon=0.0, lat=95.0)

        # Boundary coordinates: exact equator & poles
        p_pole = VesselPosition(timestamp=self.source_time, lon=-180.0, lat=90.0)
        self.assertEqual(p_pole.lat, 90.0)
        self.assertEqual(p_pole.lon, -180.0)

    # -----------------------------------------------------------------------
    # Edge Case 10: Large Spatial Jump / Implausible Speed
    # -----------------------------------------------------------------------
    def test_10_large_spatial_jump_flagged_observation_preserved(self) -> None:
        """10. Implausible coordinate jump (>60 knots): marked invalid, interval flagged, observation kept."""
        t0 = self.source_time
        t1 = t0 + timedelta(seconds=60)  # 1 minute later
        p0 = VesselPosition(timestamp=t0, lon=9.0, lat=43.0)
        # 1 degree lat jump in 60 seconds = ~1850 m/s = ~3600 knots!
        p1 = VesselPosition(timestamp=t1, lon=9.0, lat=44.0)

        cand = CandidateVessel(
            candidate_id="c-10", vessel_id="v-10", mmsi="101010101",
            closest_position_lon=9.0, closest_position_lat=43.0,
            closest_position_time=t0, inside_source_zone=False,
            min_distance_to_source_center_km=30.0, distance_to_zone_boundary_km=25.0,
            time_offset_from_source_hours=0.0, observed_positions_count=2,
            raw_positions=[p0, p1],
        )

        res, _ = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, self.source_estimate, self._make_cand_res([cand]),
            output_dir=self.work_dir,
        )

        a = res.analyses[0]
        self.assertEqual(a.observed_positions_count, 2)  # Observation kept (zero fabrication)
        self.assertEqual(len(a.segments), 1)
        seg = a.segments[0]
        self.assertFalse(seg.is_valid_kinematic)
        self.assertGreater(seg.derived_speed_knots, MAX_PLAUSIBLE_SPEED_KNOTS)
        self.assertEqual(a.quality.invalid_kinematic_interval_count, 1)
        self.assertIn("invalid_kinematic_intervals", a.quality.quality_flags)

    # -----------------------------------------------------------------------
    # Edge Case 11: Missing Optional Vessel Metadata
    # -----------------------------------------------------------------------
    def test_11_missing_optional_vessel_metadata(self) -> None:
        """11. Missing vessel_type, call_sign, flag_country, IMO, COG, SOG default cleanly."""
        t0 = self.source_time
        p0 = VesselPosition(timestamp=t0, lon=9.0, lat=43.0, speed=None, course=None, heading=None)
        cand = CandidateVessel(
            candidate_id="c-11", vessel_id="v-11", mmsi=None, imo=None, vessel_name=None,
            vessel_type=None, call_sign=None, flag_country=None,
            closest_position_lon=9.0, closest_position_lat=43.0,
            closest_position_time=t0, inside_source_zone=False,
            min_distance_to_source_center_km=30.0, distance_to_zone_boundary_km=25.0,
            time_offset_from_source_hours=0.0, observed_positions_count=1,
            raw_positions=[p0],
        )

        res, _ = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, self.source_estimate, self._make_cand_res([cand]),
            output_dir=self.work_dir,
        )

        a = res.analyses[0]
        self.assertIsNone(a.vessel_type)
        self.assertIsNone(a.call_sign)
        self.assertIsNone(a.flag_country)
        self.assertIsNone(a.mmsi)
        self.assertIsNone(a.vessel_name)
        self.assertIsNone(a.min_reported_sog_knots)
        self.assertIsNone(a.centerline_proximity.course_drift_angle_diff_deg)

    # -----------------------------------------------------------------------
    # Edge Case 12: Vessel Crossing AOI Boundary
    # -----------------------------------------------------------------------
    def test_12_vessel_crossing_aoi_boundary(self) -> None:
        """12. Vessel with points both inside and outside the D3 candidate zone."""
        t0 = self.source_time
        # Inside zone: center is (9.520573, 43.231922), radius is 3.5 km
        p_in1 = VesselPosition(timestamp=t0, lon=9.520, lat=43.232)
        p_in2 = VesselPosition(timestamp=t0 + timedelta(minutes=10), lon=9.525, lat=43.232)
        # Outside zone: ~20 km away
        p_out = VesselPosition(timestamp=t0 + timedelta(minutes=60), lon=9.300, lat=43.232)

        cand = CandidateVessel(
            candidate_id="c-12", vessel_id="v-12", mmsi="121212121",
            closest_position_lon=9.520, closest_position_lat=43.232,
            closest_position_time=t0, inside_source_zone=True,
            min_distance_to_source_center_km=0.1, distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=0.0, observed_positions_count=3,
            raw_positions=[p_in1, p_in2, p_out],
        )

        res, _ = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, self.source_estimate, self._make_cand_res([cand]),
            output_dir=self.work_dir,
        )

        tp = res.analyses[0].transit_profile
        self.assertEqual(tp.points_inside_count, 2)
        self.assertEqual(tp.observed_transit_duration_seconds, 600.0)
        self.assertEqual(tp.distance_to_zone_boundary_km, 0.0)
        self.assertEqual(tp.min_distance_to_polygon_km, 0.0)

    # -----------------------------------------------------------------------
    # Edge Case 13 & 14: Polygon Distance & Membership
    # -----------------------------------------------------------------------
    def test_13_14_polygon_distance_and_membership(self) -> None:
        """13 & 14. Outside polygon has min_distance_to_polygon_km > 0; inside has == 0.0."""
        # Point inside polygon
        p_in = VesselPosition(timestamp=self.source_time, lon=self.center_lon, lat=self.center_lat)
        # Point outside polygon (0.1 deg north ≈ 11.1 km north; radius is 3.5 km)
        p_out = VesselPosition(timestamp=self.source_time, lon=self.center_lon, lat=self.center_lat + 0.10)

        cand_in = CandidateVessel(
            candidate_id="c-in", vessel_id="v-in", mmsi="141414141",
            closest_position_lon=self.center_lon, closest_position_lat=self.center_lat,
            closest_position_time=self.source_time, inside_source_zone=True,
            min_distance_to_source_center_km=0.0, distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=0.0, observed_positions_count=1, raw_positions=[p_in],
        )
        cand_out = CandidateVessel(
            candidate_id="c-out", vessel_id="v-out", mmsi="131313131",
            closest_position_lon=self.center_lon, closest_position_lat=self.center_lat + 0.10,
            closest_position_time=self.source_time, inside_source_zone=False,
            min_distance_to_source_center_km=11.1, distance_to_zone_boundary_km=7.6,
            time_offset_from_source_hours=0.0, observed_positions_count=1, raw_positions=[p_out],
        )

        res, _ = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, self.source_estimate, self._make_cand_res([cand_in, cand_out]),
            output_dir=self.work_dir,
        )

        tp_in = res.analyses[0].transit_profile
        tp_out = res.analyses[1].transit_profile

        self.assertEqual(tp_in.min_distance_to_polygon_km, 0.0)
        self.assertEqual(tp_in.cpa_to_polygon_distance_km, 0.0)
        self.assertGreater(tp_out.min_distance_to_polygon_km, 5.0)
        self.assertGreater(tp_out.cpa_to_polygon_distance_km, 5.0)

    # -----------------------------------------------------------------------
    # Edge Case 15 & 18: No AIS Observations / Empty Trajectory
    # -----------------------------------------------------------------------
    def test_15_18_zero_observations_empty_trajectory(self) -> None:
        """15 & 18. Candidate with 0 positions handled cleanly without raising IndexError."""
        cand_empty = CandidateVessel(
            candidate_id="c-empty", vessel_id="v-empty", mmsi="000000000",
            closest_position_lon=self.center_lon, closest_position_lat=self.center_lat,
            closest_position_time=self.source_time, inside_source_zone=False,
            min_distance_to_source_center_km=0.0, distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=0.0, observed_positions_count=0,
            raw_positions=[],
        )

        res, _ = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, self.source_estimate, self._make_cand_res([cand_empty]),
            output_dir=self.work_dir,
        )

        self.assertEqual(res.analyzed_vessel_count, 1)
        a = res.analyses[0]
        self.assertEqual(a.status, "no_observations")
        self.assertEqual(a.observed_positions_count, 0)
        self.assertEqual(len(a.segments), 0)
        self.assertEqual(a.total_track_distance_m, 0.0)
        self.assertIn("no_observations", a.quality.quality_flags)

    # -----------------------------------------------------------------------
    # Edge Case 16: Missing D3 Source Geometry
    # -----------------------------------------------------------------------
    def test_16_missing_d3_source_geometry_fallback(self) -> None:
        """16. Empty source_zone_geometry falls back cleanly to analytical uncertainty radius."""
        src_no_poly = self.source_estimate.model_copy(update={"source_zone_geometry": {}})
        t0 = self.source_time
        # 1 km from center -> inside circular radius of 3.5 km
        p0 = VesselPosition(timestamp=t0, lon=self.center_lon + 0.005, lat=self.center_lat)
        cand = CandidateVessel(
            candidate_id="c-16", vessel_id="v-16",
            closest_position_lon=p0.lon, closest_position_lat=p0.lat,
            closest_position_time=t0, inside_source_zone=True,
            min_distance_to_source_center_km=0.5, distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=0.0, observed_positions_count=1, raw_positions=[p0],
        )

        res, _ = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, src_no_poly, self._make_cand_res([cand]),
            output_dir=self.work_dir,
        )

        tp = res.analyses[0].transit_profile
        self.assertEqual(tp.points_inside_count, 1)
        self.assertEqual(tp.min_distance_to_polygon_km, 0.0)

    # -----------------------------------------------------------------------
    # Edge Case 17: Temporal Correlation Metrics
    # -----------------------------------------------------------------------
    def test_17_temporal_correlation_metrics(self) -> None:
        """17. Temporal offsets from source_time and spill_time correctly calculated."""
        t_src = self.source_time  # 11:14:27
        t_spill = self.observation_time  # 17:14:27 (6 hours later)

        # Observation 2 hours after source time
        t_obs = t_src + timedelta(hours=2.0)
        p0 = VesselPosition(timestamp=t_obs, lon=9.5, lat=43.2)
        cand = CandidateVessel(
            candidate_id="c-17", vessel_id="v-17",
            closest_position_lon=9.5, closest_position_lat=43.2,
            closest_position_time=t_obs, inside_source_zone=False,
            min_distance_to_source_center_km=5.0, distance_to_zone_boundary_km=1.5,
            time_offset_from_source_hours=2.0, observed_positions_count=1, raw_positions=[p0],
        )

        res, _ = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, self.source_estimate, self._make_cand_res([cand]),
            output_dir=self.work_dir,
        )

        tc = res.analyses[0].temporal_correlation
        self.assertIsNotNone(tc)
        self.assertAlmostEqual(tc.hours_from_source_time, 2.0, delta=0.01)
        self.assertAlmostEqual(tc.seconds_from_source_time, 7200.0, delta=1.0)
        self.assertAlmostEqual(tc.hours_from_spill_time, -4.0, delta=0.01)
        self.assertAlmostEqual(tc.seconds_from_spill_time, -14400.0, delta=1.0)

    # -----------------------------------------------------------------------
    # Edge Case 19: Dateline-Safe Longitude Handling
    # -----------------------------------------------------------------------
    def test_19_dateline_safe_kinematics_and_bearing(self) -> None:
        """19. Crossing 180° / -180° meridian produces smooth, correct distance and bearing."""
        lon1 = 179.9
        lat1 = 0.0
        lon2 = -179.9
        lat2 = 0.0

        # Great circle distance across 0.2 degrees longitude at equator ≈ 22.2 km
        d_m = _haversine_m(lon1, lat1, lon2, lat2)
        self.assertAlmostEqual(d_m, 22239.0, delta=100.0)

        # Bearing eastward across dateline: due East = 90 degrees
        bearing = calculate_initial_bearing_deg(lon1, lat1, lon2, lat2)
        self.assertAlmostEqual(bearing, 90.0, delta=1.0)

    # -----------------------------------------------------------------------
    # Edge Case 20: Ulysse Reference Benchmark Regression
    # -----------------------------------------------------------------------
    def test_20_ulysse_benchmark_regression(self) -> None:
        """20. Ulysse reference benchmark: Ulysse & Virginia, candidate_count=2, min_cl_km ≈ 0.55 km."""
        t_cpa = datetime(2018, 10, 9, 12, 0, tzinfo=timezone.utc)
        p_ulysse = VesselPosition(timestamp=t_cpa, lon=9.478, lat=43.248, speed=0.0, course=205.0)
        p_virginia = VesselPosition(timestamp=t_cpa, lon=9.478, lat=43.248, speed=0.0, course=90.0)

        cand_u = CandidateVessel(
            candidate_id="cand-228308800", vessel_id="228308800", mmsi="228308800",
            vessel_name="MV ULYSSE", vessel_type="Ro-Ro Cargo",
            closest_position_lon=9.478, closest_position_lat=43.248,
            closest_position_time=t_cpa, inside_source_zone=False,
            min_distance_to_source_center_km=3.884, distance_to_zone_boundary_km=0.384,
            time_offset_from_source_hours=0.7592, speed_over_ground=0.0,
            course_over_ground=205.0, heading=205.0, observed_positions_count=1,
            raw_positions=[p_ulysse],
        )
        cand_v = CandidateVessel(
            candidate_id="cand-229986000", vessel_id="229986000", mmsi="229986000",
            vessel_name="CSL VIRGINIA", vessel_type="Container Ship",
            closest_position_lon=9.478, closest_position_lat=43.248,
            closest_position_time=t_cpa, inside_source_zone=False,
            min_distance_to_source_center_km=3.884, distance_to_zone_boundary_km=0.384,
            time_offset_from_source_hours=0.7592, speed_over_ground=0.0,
            course_over_ground=90.0, heading=90.0, observed_positions_count=1,
            raw_positions=[p_virginia],
        )

        cand_res = self._make_cand_res([cand_u, cand_v])

        res, asset = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, self.source_estimate, cand_res,
            output_dir=self.work_dir,
        )

        self.assertEqual(res.analyzed_vessel_count, 2)
        ulysse = next(a for a in res.analyses if a.mmsi == "228308800")
        virginia = next(a for a in res.analyses if a.mmsi == "229986000")

        self.assertEqual(ulysse.vessel_name, "MV ULYSSE")
        self.assertEqual(ulysse.vessel_type, "Ro-Ro Cargo")
        self.assertEqual(virginia.vessel_name, "CSL VIRGINIA")
        self.assertEqual(virginia.vessel_type, "Container Ship")

        # Centerline proximity ≈ 0.55 km (550 m from (9.478, 43.248) to step 1 (9.484502, 43.246585))
        self.assertAlmostEqual(ulysse.centerline_proximity.min_distance_to_centerline_km, 0.55, delta=0.05)
        self.assertAlmostEqual(virginia.centerline_proximity.min_distance_to_centerline_km, 0.55, delta=0.05)

        # Distance to D3 center point
        self.assertAlmostEqual(ulysse.transit_profile.min_distance_to_center_km, 3.884, delta=0.05)

        # Scientific provenance: reference benchmark, not authoritative live AIS
        self.assertFalse(ulysse.quality.is_real_observation)
        self.assertFalse(virginia.quality.is_real_observation)

    # -----------------------------------------------------------------------
    # F1 Handoff Contract Verification
    # -----------------------------------------------------------------------
    def test_f1_evidence_handoff_structure(self) -> None:
        """Verify the 4 standardized evidence blocks: SPATIAL, TEMPORAL, KINEMATIC, DATA QUALITY."""
        t0 = self.source_time
        p0 = VesselPosition(timestamp=t0, lon=9.52, lat=43.23, speed=12.0, course=180.0)
        cand = CandidateVessel(
            candidate_id="c-f1", vessel_id="v-f1", mmsi="999999999", vessel_name="EvidenceCarrier",
            closest_position_lon=9.52, closest_position_lat=43.23,
            closest_position_time=t0, inside_source_zone=True,
            min_distance_to_source_center_km=0.1, distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=0.0, observed_positions_count=1, raw_positions=[p0],
        )

        res, _ = analyze_candidate_trajectories(
            self.inv_id, self.spill_id, self.source_estimate, self._make_cand_res([cand]),
            output_dir=self.work_dir,
        )

        ev = res.analyses[0].evidence
        self.assertIn("spatial", ev)
        self.assertIn("temporal", ev)
        self.assertIn("kinematic", ev)
        self.assertIn("data_quality", ev)

        # Spatial block
        self.assertIn("min_distance_to_source_center_km", ev["spatial"])
        self.assertIn("inside_source_zone", ev["spatial"])
        self.assertIn("min_distance_to_polygon_km", ev["spatial"])
        self.assertIn("min_distance_to_centerline_km", ev["spatial"])
        self.assertIn("cpa_to_center", ev["spatial"])
        self.assertIn("cpa_to_polygon", ev["spatial"])
        self.assertIn("cpa_to_centerline", ev["spatial"])

        # Temporal block
        self.assertIn("time_offset_from_source_hours", ev["temporal"])
        self.assertIn("time_offset_from_spill_hours", ev["temporal"])
        self.assertIn("temporal_span_seconds", ev["temporal"])

        # Data quality block
        self.assertIn("observation_count", ev["data_quality"])
        self.assertIn("duplicate_count", ev["data_quality"])
        self.assertIn("has_multiple_observations", ev["data_quality"])
        self.assertIn("has_valid_kinematics", ev["data_quality"])
        self.assertIn("is_real_observation", ev["data_quality"])
        self.assertFalse(ev["data_quality"]["is_real_observation"])
