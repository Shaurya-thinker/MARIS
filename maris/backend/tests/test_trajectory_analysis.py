"""Hermetic tests for MARIS Stage E2 — AIS Trajectory & Spatial/Temporal Analysis.

All tests are completely offline and hermetic.
Verifies:
- Pairwise trajectory segment calculations (distance, duration, derived speed, reported SOG)
- Zero-fabrication invariant (no intermediate ping synthesis; gaps never filled; single ping handling)
- Source candidate zone transit profiles (points inside, transit duration strictly t_last - t_first)
- Proximity to D3 backward drift centerline (point-to-polyline projection)
- Raw COG vs drift-direction circular angular comparison without behavioural scoring
- Track-level statistics aggregation (min, max, mean SOG, mean derived speed)
- GeoJSON export and AssetRegistry DOCUMENT asset registration
- Edge cases: empty candidates, missing COG, 1-point vessels
- API endpoints: 200 OK with valid result, 404 on missing assets
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.acquisition.registry import default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.main import app
from app.models.asset import Asset
from app.models.common import AssetType, Provenance
from app.models.source_estimation import BackwardDriftStep, SourceEstimateResult
from app.models.trajectory_analysis import (
    TrajectoryAnalysisResult,
    TrajectorySegment,
    VesselTrajectoryAnalysis,
)
from app.models.vessel import (
    CandidateVessel,
    CandidateVesselGenerationResult,
    VesselPosition,
)
from app.services.drift_modelling import _haversine_m
from app.services.source_estimation import generate_source_candidate_polygon
from app.services.trajectory_analysis import (
    TrajectoryAnalysisError,
    analyze_candidate_trajectories,
    angular_difference_deg,
    compute_drift_direction_deg,
    point_to_polyline_distance_m,
    point_to_segment_distance_m,
)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Helper to build CandidateVesselGenerationResult fixtures
# ---------------------------------------------------------------------------

def _make_cand_result(
    investigation_id: str,
    spill_id: str,
    source_estimate_id: str,
    source_time: datetime,
    uncertainty_radius_km: float,
    candidates: list[CandidateVessel],
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> CandidateVesselGenerationResult:
    """Build a CandidateVesselGenerationResult with correct enum values."""
    return CandidateVesselGenerationResult(
        id=f"cand-gen-test-{spill_id}",
        investigation_id=investigation_id,
        source_estimate_id=source_estimate_id,
        spill_detection_id=spill_id,
        status="completed" if candidates else "no_candidates_found",
        source_time=source_time,
        source_uncertainty_radius_km=uncertainty_radius_km,
        temporal_window_start=window_start or source_time,
        temporal_window_end=window_end or source_time,
        spatial_query_bbox={},
        candidate_count=len(candidates),
        total_vessels_checked=max(len(candidates), 1),
        candidates=candidates,
    )


class TrajectoryAnalysisBaseTest(unittest.TestCase):
    """Base setup with synthetic D3 source estimate and E1 candidate vessel generation results."""

    def setUp(self) -> None:
        default_asset_registry._assets.clear()
        default_asset_registry._order.clear()

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp_dir.name)

        self.investigation_id = "inv-traj-test"
        self.spill_id = "spill-traj-001"

        # D3 center at lon=2.0, lat=51.0; spill origin at lon=2.2, lat=51.1
        self.center_lon = 2.0
        self.center_lat = 51.0
        self.uncertainty_radius_km = 5.0

        self.source_time = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)
        self.observation_time = datetime(2024, 6, 2, 6, 0, tzinfo=timezone.utc)

        # 32-vertex regular polygon for 5000 m radius
        self.source_polygon = generate_source_candidate_polygon(
            center_lon=self.center_lon,
            center_lat=self.center_lat,
            radius_m=self.uncertainty_radius_km * 1000.0,
            num_vertices=32,
        )

        # BackwardDriftSteps for the D3 centerline
        self.drift_steps = [
            BackwardDriftStep(
                timestamp=self.observation_time,
                lon=2.2, lat=51.1,
                u_wind_ms=-3.0, v_wind_ms=-2.0,
                u_current_ms=-0.2, v_current_ms=-0.1,
                drift_u_ms=-0.305, drift_v_ms=-0.170,
                cumulative_backward_distance_m=0.0,
                uncertainty_radius_m=500.0,
            ),
            BackwardDriftStep(
                timestamp=datetime(2024, 6, 2, 3, 0, tzinfo=timezone.utc),
                lon=2.1, lat=51.05,
                u_wind_ms=-3.0, v_wind_ms=-2.0,
                u_current_ms=-0.2, v_current_ms=-0.1,
                drift_u_ms=-0.305, drift_v_ms=-0.170,
                cumulative_backward_distance_m=10000.0,
                uncertainty_radius_m=2500.0,
            ),
            BackwardDriftStep(
                timestamp=self.source_time,
                lon=self.center_lon, lat=self.center_lat,
                u_wind_ms=-3.0, v_wind_ms=-2.0,
                u_current_ms=-0.2, v_current_ms=-0.1,
                drift_u_ms=-0.305, drift_v_ms=-0.170,
                cumulative_backward_distance_m=20000.0,
                uncertainty_radius_m=5000.0,
            ),
        ]

        # D3 SourceEstimateResult
        self.source_estimate = SourceEstimateResult(
            id=f"source-{self.spill_id}-{self.investigation_id}",
            investigation_id=self.investigation_id,
            spill_detection_id=self.spill_id,
            wind_asset_id="wind-asset-001",
            current_asset_id="curr-asset-001",
            asset_id="drift-asset-001",
            model_version="leeway_euler_backward_v1",
            observation_time=self.observation_time,
            origin_lon=2.2, origin_lat=51.1,
            source_time=self.source_time,
            source_point_lon=self.center_lon,
            source_point_lat=self.center_lat,
            lookback_hours=6.0, step_hours=3.0, leeway_fraction=0.035,
            source_uncertainty_radius_km=self.uncertainty_radius_km,
            steps=self.drift_steps,
            source_zone_geometry=self.source_polygon,
            metadata={"test": "fixture"},
        )

        # Write and register source estimate GeoJSON
        self.source_geojson_path = self.work_dir / "source_candidate_zone.geojson"
        feat_collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[2.2, 51.1], [2.1, 51.05], [self.center_lon, self.center_lat]],
                    },
                    "properties": {
                        "feature_kind": "backward_drift_centerline",
                        "investigation_id": self.investigation_id,
                        "spill_detection_id": self.spill_id,
                        "observation_time": self.observation_time.isoformat(),
                        "source_time": self.source_time.isoformat(),
                        "lookback_hours": 6.0,
                    },
                },
                {
                    "type": "Feature",
                    "geometry": self.source_polygon,
                    "properties": {
                        "feature_kind": "source_candidate_zone",
                        "investigation_id": self.investigation_id,
                        "spill_detection_id": self.spill_id,
                        "source_time": self.source_time.isoformat(),
                        "center_lon": self.center_lon,
                        "center_lat": self.center_lat,
                        "uncertainty_radius_km": self.uncertainty_radius_km,
                    },
                },
            ],
        }
        self.source_geojson_path.write_text(json.dumps(feat_collection, indent=2), encoding="utf-8")

        self.source_asset = default_asset_registry.register(
            self.investigation_id,
            "source_estimation",
            AcquiredArtifact(
                asset_type=AssetType.DRIFT_PRODUCT,
                location=str(self.source_geojson_path),
                source="test",
                provenance=Provenance(
                    product_id=self.spill_id,
                    extra={
                        "source_id": self.source_estimate.id,
                        "source_estimate_id": self.source_estimate.id,
                        "observation_time": self.observation_time.isoformat(),
                        "source_time": self.source_time.isoformat(),
                        "lookback_hours": 6.0,
                        "step_hours": 3.0,
                        "leeway_fraction": 0.035,
                        "source_uncertainty_radius_km": self.uncertainty_radius_km,
                    },
                ),
                metadata={"source_id": self.source_estimate.id},
            ),
        )

        # Register spill asset
        self.spill_asset = default_asset_registry.register(
            self.investigation_id,
            "spill_detector",
            AcquiredArtifact(
                asset_type=AssetType.SPILL_GEOMETRY,
                location=str(self.work_dir / "spill.geojson"),
                source="test",
                acquisition_time=self.observation_time,
                provenance=Provenance(product_id=self.spill_id),
                metadata={"spill_id": self.spill_id, "detection_id": self.spill_id},
            ),
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()
        default_asset_registry._assets.clear()
        default_asset_registry._order.clear()


class TrajectoryAnalysisServiceTests(TrajectoryAnalysisBaseTest):
    """Detailed unit tests for trajectory analysis logic, kinematics, and invariants."""

    def test_pairwise_trajectory_segments_distance_and_duration(self) -> None:
        """Sequential pairwise segments correctly compute haversine distance and duration seconds."""
        t0 = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2024, 6, 2, 0, 30, tzinfo=timezone.utc)
        t2 = datetime(2024, 6, 2, 1, 0, tzinfo=timezone.utc)

        p0 = VesselPosition(timestamp=t0, lon=2.00, lat=51.00, speed=10.0, course=90.0)
        p1 = VesselPosition(timestamp=t1, lon=2.05, lat=51.00, speed=10.2, course=90.0)
        p2 = VesselPosition(timestamp=t2, lon=2.10, lat=51.00, speed=9.8, course=90.0)

        cand = CandidateVessel(
            candidate_id="cand-01", vessel_id="VESSEL-A", mmsi="123456789",
            vessel_name="Alpha",
            closest_position_lon=2.00, closest_position_lat=51.00,
            closest_position_time=t0,
            inside_source_zone=True,
            min_distance_to_source_center_km=0.0,
            distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=0.0,
            speed_over_ground=10.0, course_over_ground=90.0,
            observed_positions_count=3,
            raw_positions=[p0, p1, p2],
        )

        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km, [cand],
            window_start=t0, window_end=t2,
        )

        res, asset = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        self.assertEqual(res.analyzed_vessel_count, 1)
        analysis = res.analyses[0]
        self.assertEqual(len(analysis.segments), 2)

        seg0 = analysis.segments[0]
        self.assertEqual(seg0.start_time, t0)
        self.assertEqual(seg0.end_time, t1)
        self.assertEqual(seg0.duration_seconds, 1800.0)
        expected_dist_0 = _haversine_m(2.00, 51.00, 2.05, 51.00)
        self.assertAlmostEqual(seg0.distance_m, expected_dist_0, delta=1.0)

        seg1 = analysis.segments[1]
        self.assertEqual(seg1.start_time, t1)
        self.assertEqual(seg1.end_time, t2)
        self.assertEqual(seg1.duration_seconds, 1800.0)

    def test_derived_speed_and_sog_difference(self) -> None:
        """Average derived speed (dist/dt) in knots correctly calculated and compared against reported SOG."""
        t0 = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2024, 6, 2, 1, 0, tzinfo=timezone.utc)

        dlon = 1.0 / 60.0  # 1 nautical mile at equator
        p0 = VesselPosition(timestamp=t0, lon=0.0, lat=0.0, speed=1.2)
        p1 = VesselPosition(timestamp=t1, lon=dlon, lat=0.0, speed=1.4)

        cand = CandidateVessel(
            candidate_id="cand-02", vessel_id="VESSEL-B", mmsi="222222222",
            closest_position_lon=0.0, closest_position_lat=0.0,
            closest_position_time=t0,
            inside_source_zone=False,
            min_distance_to_source_center_km=50.0,
            distance_to_zone_boundary_km=45.0,
            time_offset_from_source_hours=0.0,
            observed_positions_count=2,
            raw_positions=[p0, p1],
        )

        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km, [cand],
            window_start=t0, window_end=t1,
        )

        res, _ = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        seg = res.analyses[0].segments[0]
        self.assertIsNotNone(seg.derived_speed_knots)
        self.assertAlmostEqual(seg.derived_speed_knots, 1.0, delta=0.05)
        self.assertIsNotNone(seg.sog_difference_knots)
        self.assertAlmostEqual(seg.sog_difference_knots, 0.3, delta=0.05)

    def test_single_observation_vessel_handling(self) -> None:
        """Vessel with exactly 1 observed ping produces segments=[], total_track_distance=0, total_duration=0."""
        t0 = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)
        p0 = VesselPosition(timestamp=t0, lon=2.00, lat=51.00, speed=8.5)

        cand = CandidateVessel(
            candidate_id="cand-single", vessel_id="VESSEL-SINGLE", mmsi="333333333",
            closest_position_lon=2.00, closest_position_lat=51.00,
            closest_position_time=t0,
            inside_source_zone=True,
            min_distance_to_source_center_km=0.0,
            distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=0.0,
            observed_positions_count=1,
            raw_positions=[p0],
        )

        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km, [cand],
        )

        res, _ = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        analysis = res.analyses[0]
        self.assertEqual(analysis.observed_positions_count, 1)
        self.assertEqual(len(analysis.segments), 0)
        self.assertEqual(analysis.total_track_distance_m, 0.0)
        self.assertEqual(analysis.total_track_duration_seconds, 0.0)
        self.assertIsNone(analysis.mean_derived_speed_knots)
        self.assertEqual(analysis.mean_reported_sog_knots, 8.5)

    def test_zero_fabrication_no_gap_interpolation(self) -> None:
        """Large temporal gap produces 1 pairwise segment and never interpolates pings."""
        t0 = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)
        t_gap = datetime(2024, 6, 2, 5, 0, tzinfo=timezone.utc)

        p0 = VesselPosition(timestamp=t0, lon=2.00, lat=51.00, speed=10.0)
        p1 = VesselPosition(timestamp=t_gap, lon=2.50, lat=51.20, speed=10.0)

        cand = CandidateVessel(
            candidate_id="cand-gap", vessel_id="VESSEL-GAP",
            closest_position_lon=2.00, closest_position_lat=51.00,
            closest_position_time=t0,
            inside_source_zone=True,
            min_distance_to_source_center_km=0.0,
            distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=0.0,
            observed_positions_count=2,
            raw_positions=[p0, p1],
        )

        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km, [cand],
            window_start=t0, window_end=t_gap,
        )

        res, _ = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        analysis = res.analyses[0]
        self.assertEqual(len(analysis.segments), 1)
        self.assertEqual(analysis.segments[0].duration_seconds, 18000.0)
        self.assertEqual(analysis.observed_positions_count, 2)

    def test_zone_transit_duration_multi_point(self) -> None:
        """Multi-point transit: observed_transit_duration_seconds == t_last - t_first."""
        t0 = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2024, 6, 2, 0, 20, tzinfo=timezone.utc)
        t2 = datetime(2024, 6, 2, 0, 45, tzinfo=timezone.utc)
        t_out = datetime(2024, 6, 2, 2, 0, tzinfo=timezone.utc)

        # Inside zone (center=2.0, 51.0; radius=5km ~0.045 deg lat)
        p0 = VesselPosition(timestamp=t0, lon=2.001, lat=51.001)
        p1 = VesselPosition(timestamp=t1, lon=2.005, lat=51.005)
        p2 = VesselPosition(timestamp=t2, lon=2.010, lat=51.010)
        p_out = VesselPosition(timestamp=t_out, lon=2.500, lat=51.500)

        cand = CandidateVessel(
            candidate_id="cand-multi-in", vessel_id="VESSEL-MULTI",
            closest_position_lon=2.001, closest_position_lat=51.001,
            closest_position_time=t0,
            inside_source_zone=True,
            min_distance_to_source_center_km=0.1,
            distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=0.0,
            observed_positions_count=4,
            raw_positions=[p0, p1, p2, p_out],
        )

        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km, [cand],
            window_start=t0, window_end=t_out,
        )

        res, _ = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        profile = res.analyses[0].transit_profile
        self.assertEqual(profile.points_inside_count, 3)
        self.assertEqual(profile.first_inside_time, t0)
        self.assertEqual(profile.last_inside_time, t2)
        self.assertEqual(profile.observed_transit_duration_seconds, 2700.0)

    def test_zone_transit_single_inside_point_zero_duration(self) -> None:
        """Only 1 point inside the zone: transit duration strictly 0.0 seconds."""
        t0 = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)
        t_out = datetime(2024, 6, 2, 1, 0, tzinfo=timezone.utc)

        p_in = VesselPosition(timestamp=t0, lon=2.001, lat=51.001)
        p_out = VesselPosition(timestamp=t_out, lon=2.500, lat=51.500)

        cand = CandidateVessel(
            candidate_id="cand-one-in", vessel_id="VESSEL-ONE",
            closest_position_lon=2.001, closest_position_lat=51.001,
            closest_position_time=t0,
            inside_source_zone=True,
            min_distance_to_source_center_km=0.1,
            distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=0.0,
            observed_positions_count=2,
            raw_positions=[p_in, p_out],
        )

        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km, [cand],
            window_start=t0, window_end=t_out,
        )

        res, _ = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        profile = res.analyses[0].transit_profile
        self.assertEqual(profile.points_inside_count, 1)
        self.assertEqual(profile.first_inside_time, t0)
        self.assertEqual(profile.last_inside_time, t0)
        self.assertEqual(profile.observed_transit_duration_seconds, 0.0)

    def test_zone_transit_zero_inside_points(self) -> None:
        """Vessel outside the zone: points_inside_count=0, times=None, duration=0.0."""
        t0 = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)
        p_out = VesselPosition(timestamp=t0, lon=2.500, lat=51.500)

        cand = CandidateVessel(
            candidate_id="cand-zero-in", vessel_id="VESSEL-ZERO",
            closest_position_lon=2.500, closest_position_lat=51.500,
            closest_position_time=t0,
            inside_source_zone=False,
            min_distance_to_source_center_km=50.0,
            distance_to_zone_boundary_km=45.0,
            time_offset_from_source_hours=0.0,
            observed_positions_count=1,
            raw_positions=[p_out],
        )

        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km, [cand],
        )

        res, _ = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        profile = res.analyses[0].transit_profile
        self.assertEqual(profile.points_inside_count, 0)
        self.assertIsNone(profile.first_inside_time)
        self.assertIsNone(profile.last_inside_time)
        self.assertEqual(profile.observed_transit_duration_seconds, 0.0)

    def test_min_distance_to_center_and_cpa(self) -> None:
        """CPA coordinates, min distance to D3 source center, and time_offset_from_source_hours are correct."""
        t_early = datetime(2024, 6, 1, 23, 0, tzinfo=timezone.utc)
        t_cpa = datetime(2024, 6, 2, 1, 30, tzinfo=timezone.utc)

        p_far = VesselPosition(timestamp=t_early, lon=2.2, lat=51.2)
        p_close = VesselPosition(timestamp=t_cpa, lon=2.01, lat=51.01)

        cand = CandidateVessel(
            candidate_id="cand-cpa", vessel_id="VESSEL-CPA",
            closest_position_lon=2.01, closest_position_lat=51.01,
            closest_position_time=t_cpa,
            inside_source_zone=True,
            min_distance_to_source_center_km=1.3,
            distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=1.5,
            observed_positions_count=2,
            raw_positions=[p_far, p_close],
        )

        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km, [cand],
            window_start=t_early, window_end=t_cpa,
        )

        res, _ = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        profile = res.analyses[0].transit_profile
        self.assertEqual(profile.cpa_lon, 2.01)
        self.assertEqual(profile.cpa_lat, 51.01)
        self.assertEqual(profile.closest_position_time, t_cpa)
        self.assertAlmostEqual(profile.time_offset_from_source_hours, 1.5, delta=0.01)
        expected_km = _haversine_m(2.01, 51.01, 2.0, 51.0) / 1000.0
        self.assertAlmostEqual(profile.min_distance_to_center_km, expected_km, delta=0.05)

    def test_distance_to_zone_boundary_inside_vs_outside(self) -> None:
        """distance_to_zone_boundary_km is 0.0 inside the zone and > 0.0 outside."""
        t0 = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)

        p_in = VesselPosition(timestamp=t0, lon=2.001, lat=51.001)
        cand_in = CandidateVessel(
            candidate_id="cand-in", vessel_id="VESSEL-IN",
            closest_position_lon=2.001, closest_position_lat=51.001,
            closest_position_time=t0,
            inside_source_zone=True,
            min_distance_to_source_center_km=0.1,
            distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=0.0,
            observed_positions_count=1,
            raw_positions=[p_in],
        )

        # ~15 km north of center
        p_out = VesselPosition(timestamp=t0, lon=2.0, lat=51.135)
        cand_out = CandidateVessel(
            candidate_id="cand-out", vessel_id="VESSEL-OUT",
            closest_position_lon=2.0, closest_position_lat=51.135,
            closest_position_time=t0,
            inside_source_zone=False,
            min_distance_to_source_center_km=15.0,
            distance_to_zone_boundary_km=10.0,
            time_offset_from_source_hours=0.0,
            observed_positions_count=1,
            raw_positions=[p_out],
        )

        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km,
            [cand_in, cand_out],
        )

        res, _ = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        in_profile = res.analyses[0].transit_profile
        out_profile = res.analyses[1].transit_profile
        self.assertEqual(in_profile.distance_to_zone_boundary_km, 0.0)
        self.assertGreater(out_profile.distance_to_zone_boundary_km, 9.0)

    def test_centerline_point_to_polyline_projection(self) -> None:
        """point_to_polyline_distance_m correctly computes projection distance."""
        coords = [[2.0, 51.0], [2.1, 51.0], [2.2, 51.0]]

        # Point on segment
        d_m, q_lon, q_lat = point_to_polyline_distance_m(2.05, 51.0, coords)
        self.assertAlmostEqual(d_m, 0.0, delta=1.0)

        # Point perpendicular to segment (~1110 m north)
        d_m_perp, _, _ = point_to_polyline_distance_m(2.05, 51.01, coords)
        expected_dist = _haversine_m(2.05, 51.01, 2.05, 51.0)
        self.assertAlmostEqual(d_m_perp, expected_dist, delta=1.0)

    def test_centerline_proximity_profile_values(self) -> None:
        """Closest centerline point and min_distance_to_centerline_km correctly populated."""
        t0 = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)
        # Ping at (2.1, 51.06) is near the middle drift step (2.1, 51.05)
        p0 = VesselPosition(timestamp=t0, lon=2.1, lat=51.06, course=180.0)

        cand = CandidateVessel(
            candidate_id="cand-cl", vessel_id="VESSEL-CL",
            closest_position_lon=2.1, closest_position_lat=51.06,
            closest_position_time=t0,
            inside_source_zone=False,
            min_distance_to_source_center_km=10.0,
            distance_to_zone_boundary_km=5.0,
            time_offset_from_source_hours=0.0,
            observed_positions_count=1,
            raw_positions=[p0],
        )

        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km, [cand],
        )

        res, _ = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        cl = res.analyses[0].centerline_proximity
        # The centerline passes through (2.1, 51.05), and the ping is at (2.1, 51.06).
        # The projection onto the centerline should be near (2.1, 51.05).
        expected_km = _haversine_m(2.1, 51.06, 2.1, 51.05) / 1000.0
        self.assertAlmostEqual(cl.min_distance_to_centerline_km, expected_km, delta=0.5)
        self.assertAlmostEqual(cl.closest_centerline_point_lat, 51.05, delta=0.02)

    def test_raw_cog_drift_angle_comparison(self) -> None:
        """Circular angular difference in [0, 180] deg correctly computed."""
        self.assertEqual(angular_difference_deg(10.0, 20.0), 10.0)
        self.assertEqual(angular_difference_deg(350.0, 10.0), 20.0)
        self.assertEqual(angular_difference_deg(90.0, 270.0), 180.0)
        self.assertEqual(angular_difference_deg(0.0, 180.0), 180.0)
        self.assertEqual(angular_difference_deg(45.0, 45.0), 0.0)

        # Drift direction helpers
        self.assertEqual(compute_drift_direction_deg(-1.0, 0.0), 270.0)
        self.assertEqual(compute_drift_direction_deg(0.0, 1.0), 0.0)

        # Full integration test
        t0 = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)
        p0 = VesselPosition(timestamp=t0, lon=2.0, lat=51.0, course=250.0)
        cand = CandidateVessel(
            candidate_id="cand-angle", vessel_id="VESSEL-ANGLE",
            closest_position_lon=2.0, closest_position_lat=51.0,
            closest_position_time=t0,
            inside_source_zone=True,
            min_distance_to_source_center_km=0.0,
            distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=0.0,
            course_over_ground=250.0,
            observed_positions_count=1,
            raw_positions=[p0],
        )

        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km, [cand],
        )

        res, _ = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        cl = res.analyses[0].centerline_proximity
        self.assertIsNotNone(cl.course_drift_angle_diff_deg)
        self.assertGreaterEqual(cl.course_drift_angle_diff_deg, 0.0)
        self.assertLessEqual(cl.course_drift_angle_diff_deg, 180.0)

    def test_missing_cog_leaves_angle_diff_none(self) -> None:
        """When vessel reports None for COG, course_drift_angle_diff_deg is None."""
        t0 = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)
        p0 = VesselPosition(timestamp=t0, lon=2.0, lat=51.0, course=None)
        cand = CandidateVessel(
            candidate_id="cand-no-cog", vessel_id="VESSEL-NO-COG",
            closest_position_lon=2.0, closest_position_lat=51.0,
            closest_position_time=t0,
            inside_source_zone=True,
            min_distance_to_source_center_km=0.0,
            distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=0.0,
            course_over_ground=None,
            observed_positions_count=1,
            raw_positions=[p0],
        )

        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km, [cand],
        )

        res, _ = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        self.assertIsNone(res.analyses[0].centerline_proximity.course_drift_angle_diff_deg)

    def test_track_level_statistics_aggregation(self) -> None:
        """Min, max, mean reported SOG and mean derived speed correctly aggregated."""
        t0 = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2024, 6, 2, 1, 0, tzinfo=timezone.utc)
        t2 = datetime(2024, 6, 2, 2, 0, tzinfo=timezone.utc)

        p0 = VesselPosition(timestamp=t0, lon=2.0, lat=51.0, speed=5.0)
        p1 = VesselPosition(timestamp=t1, lon=2.1, lat=51.0, speed=10.0)
        p2 = VesselPosition(timestamp=t2, lon=2.2, lat=51.0, speed=15.0)

        cand = CandidateVessel(
            candidate_id="cand-stats", vessel_id="VESSEL-STATS",
            closest_position_lon=2.0, closest_position_lat=51.0,
            closest_position_time=t0,
            inside_source_zone=True,
            min_distance_to_source_center_km=0.0,
            distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=0.0,
            observed_positions_count=3,
            raw_positions=[p0, p1, p2],
        )

        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km, [cand],
            window_start=t0, window_end=t2,
        )

        res, _ = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        a = res.analyses[0]
        self.assertEqual(a.min_reported_sog_knots, 5.0)
        self.assertEqual(a.max_reported_sog_knots, 15.0)
        self.assertEqual(a.mean_reported_sog_knots, 10.0)
        self.assertIsNotNone(a.mean_derived_speed_knots)
        self.assertEqual(a.total_track_duration_seconds, 7200.0)
        self.assertGreater(a.total_track_distance_m, 0.0)

    def test_geojson_artifact_structure_and_properties(self) -> None:
        """Exported trajectory_analysis.geojson contains FeatureCollection with zero_fabrication flag."""
        t0 = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2024, 6, 2, 0, 30, tzinfo=timezone.utc)
        p0 = VesselPosition(timestamp=t0, lon=2.0, lat=51.0, speed=10.0, course=90.0)
        p1 = VesselPosition(timestamp=t1, lon=2.05, lat=51.0, speed=10.0, course=90.0)

        cand = CandidateVessel(
            candidate_id="cand-geojson", vessel_id="VESSEL-GEO", mmsi="987654321",
            closest_position_lon=2.0, closest_position_lat=51.0,
            closest_position_time=t0,
            inside_source_zone=True,
            min_distance_to_source_center_km=0.0,
            distance_to_zone_boundary_km=0.0,
            time_offset_from_source_hours=0.0,
            observed_positions_count=2,
            raw_positions=[p0, p1],
        )

        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km, [cand],
            window_start=t0, window_end=t1,
        )

        res, asset = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        geojson_file = Path(asset.location)
        self.assertTrue(geojson_file.exists())
        content = json.loads(geojson_file.read_text(encoding="utf-8"))

        self.assertEqual(content["type"], "FeatureCollection")
        kinds = [f["properties"].get("feature_kind") for f in content["features"]]
        self.assertIn("backward_drift_centerline", kinds)
        self.assertIn("source_candidate_zone", kinds)
        self.assertIn("candidate_trajectory", kinds)
        self.assertIn("closest_approach_point", kinds)
        self.assertIn("centerline_closest_point", kinds)

        traj_feat = next(f for f in content["features"] if f["properties"].get("feature_kind") == "candidate_trajectory")
        self.assertTrue(traj_feat["properties"].get("zero_fabrication"))
        self.assertEqual(traj_feat["geometry"]["type"], "LineString")
        self.assertEqual(len(traj_feat["geometry"]["coordinates"]), 2)

    def test_asset_registry_registration_document_type(self) -> None:
        """Registered asset has AssetType.DOCUMENT and metadata asset_type=trajectory_analysis."""
        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km, [],
        )

        res, asset = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        self.assertEqual(asset.type, AssetType.DOCUMENT)
        self.assertEqual(asset.metadata.get("asset_type"), "trajectory_analysis")
        self.assertEqual(asset.investigation_id, self.investigation_id)
        self.assertEqual(asset.id, res.derived_asset_id)

        retrieved = default_asset_registry.get(asset.id)
        self.assertEqual(retrieved.id, asset.id)

    def test_empty_candidate_list_handling(self) -> None:
        """Empty candidate list: analyzed_vessel_count=0 and artifact still registered."""
        cand_result = _make_cand_result(
            self.investigation_id, self.spill_id, self.source_estimate.id,
            self.source_time, self.uncertainty_radius_km, [],
        )

        res, asset = analyze_candidate_trajectories(
            self.investigation_id, self.spill_id,
            self.source_estimate, cand_result,
            output_dir=self.work_dir,
        )

        self.assertEqual(res.analyzed_vessel_count, 0)
        self.assertEqual(len(res.analyses), 0)
        self.assertIsNotNone(res.derived_asset_id)
        self.assertTrue(Path(asset.location).exists())


class TrajectoryAnalysisApiTests(TrajectoryAnalysisBaseTest):
    """API endpoint tests for POST /api/v1/investigations/{inv}/spills/{spill}/trajectory-analysis."""

    def setUp(self) -> None:
        super().setUp()
        self.client = TestClient(app)

        t0 = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)
        p0 = VesselPosition(timestamp=t0, lon=2.001, lat=51.001, speed=10.0, course=90.0)
        p1 = VesselPosition(
            timestamp=datetime(2024, 6, 2, 0, 30, tzinfo=timezone.utc),
            lon=2.05, lat=51.001,
            speed=10.0, course=90.0,
        )

        cand_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [2.001, 51.001]},
                    "properties": {
                        "feature_kind": "candidate_cpa",
                        "candidate_id": "cand-api-01",
                        "vessel_id": "VESSEL-API",
                        "mmsi": "555555555",
                        "vessel_name": "ApiCarrier",
                        "inside_source_zone": True,
                        "min_distance_to_source_center_km": 0.1,
                        "distance_to_zone_boundary_km": 0.0,
                        "closest_position_time": t0.isoformat(),
                        "time_offset_from_source_hours": 0.0,
                        "speed_over_ground": 10.0,
                        "course_over_ground": 90.0,
                        "observed_positions_count": 2,
                    },
                },
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[2.001, 51.001], [2.05, 51.001]]},
                    "properties": {
                        "feature_kind": "observed_vessel_track",
                        "candidate_id": "cand-api-01",
                        "vessel_id": "VESSEL-API",
                        "mmsi": "555555555",
                        "point_count": 2,
                        "zero_fabrication": True,
                    },
                },
            ],
        }

        self.cand_geojson_path = self.work_dir / "candidate_vessels.geojson"
        self.cand_geojson_path.write_text(json.dumps(cand_geojson, indent=2), encoding="utf-8")

        self.cand_asset = default_asset_registry.register(
            self.investigation_id,
            "candidate_vessel_generation",
            AcquiredArtifact(
                asset_type=AssetType.DOCUMENT,
                location=str(self.cand_geojson_path),
                source="test",
                provenance=Provenance(
                    product_id="cand-gen-api",
                    extra={
                        "asset_type": "candidate_vessels",
                        "source_estimate_id": self.source_estimate.id,
                        "spill_detection_id": self.spill_id,
                        "candidate_generation_id": "cand-gen-api",
                    },
                ),
                metadata={
                    "asset_type": "candidate_vessels",
                    "investigation_id": self.investigation_id,
                    "spill_id": self.spill_id,
                    "source_estimate_id": self.source_estimate.id,
                    "candidate_generation_id": "cand-gen-api",
                },
            ),
        )

    def test_api_endpoint_success(self) -> None:
        """POST trajectory-analysis returns 200 with result."""
        url = f"/api/v1/investigations/{self.investigation_id}/spills/{self.spill_id}/trajectory-analysis"
        payload = {
            "source_estimate_id": self.source_estimate.id,
            "candidate_generation_id": self.cand_asset.id,
        }
        resp = self.client.post(url, json=payload)
        self.assertEqual(resp.status_code, 200, resp.text)

        data = resp.json()
        self.assertEqual(data["investigation_id"], self.investigation_id)
        self.assertEqual(data["spill_detection_id"], self.spill_id)
        self.assertEqual(data["source_estimate_id"], self.source_estimate.id)
        self.assertEqual(data["analyzed_vessel_count"], 1)
        self.assertIsNotNone(data["derived_asset_id"])

        analyses = data["analyses"]
        self.assertEqual(len(analyses), 1)
        a0 = analyses[0]
        self.assertEqual(a0["vessel_id"], "VESSEL-API")
        self.assertIn("transit_profile", a0)
        self.assertIn("centerline_proximity", a0)
        self.assertIn("segments", a0)

    def test_api_endpoint_missing_assets_404(self) -> None:
        """Non-existent spill, source estimate, or candidate asset returns 404."""
        url_base = f"/api/v1/investigations/{self.investigation_id}/spills"

        # 1. Non-existent spill
        resp = self.client.post(
            f"{url_base}/non-existent-spill/trajectory-analysis",
            json={"source_estimate_id": self.source_estimate.id},
        )
        self.assertEqual(resp.status_code, 404)

        # 2. Non-existent source estimate
        resp = self.client.post(
            f"{url_base}/{self.spill_id}/trajectory-analysis",
            json={"source_estimate_id": "non-existent-source"},
        )
        self.assertEqual(resp.status_code, 404)

        # 3. Non-existent candidate generation id
        resp = self.client.post(
            f"{url_base}/{self.spill_id}/trajectory-analysis",
            json={
                "source_estimate_id": self.source_estimate.id,
                "candidate_generation_id": "non-existent-cand-asset",
            },
        )
        self.assertEqual(resp.status_code, 404)
