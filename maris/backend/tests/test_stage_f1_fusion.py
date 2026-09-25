"""Stage F1 — Evidence Fusion & Attribution Comprehensive Test Suite.

Scientifically audits and verifies the 20 required Stage F1 behaviors:
 1. All evidence dimensions present (B3, C2, D3, E1, E2)
 2. Missing environmental data (C2 missing/unavailable handled gracefully)
 3. Missing AIS data (candidate with 0 observations)
 4. Single-observation AIS (sparse track flag, kinematics unverified)
 5. Sparse trajectory (<= 2 observations, quality flags recorded)
 6. D3 current fallback (wind-only drift fallback preserved in provenance & warnings)
 7. D3 shoreline termination (shoreline truncated flag preserved)
 8. Source uncertainty (R_zone scaling verification)
 9. Spatial normalization (exponential decay formula verification)
10. Temporal normalization (Gaussian decay formula verification)
11. Trajectory alignment normalization (combined cross-track and angular)
12. Source-zone membership (inside/outside polygon boundary semantics)
13. Evidence provenance (is_real_observation=False, data_source_type preserved)
14. No double-counting (D1 forward drift cross-check w=0.0, E3 behavioral w=0.0)
15. Unavailable vs negative evidence (missing channel excluded from denominator, never 0)
16. Benchmark Ulysse (MV ULYSSE MMSI 228308800 and CSL VIRGINIA MMSI 229986000)
17. E2 -> F1 serialization (evidence blocks, GeoJSON round-trip)
18. Backward compatibility (F2 candidate ranking and legacy fields)
19. Deterministic output (identical inputs yield identical bit-for-bit results)
20. No responsibility probability generated (no guilt, perpetrator, or legal causation)
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.acquisition.registry import InMemoryAssetRegistry
from app.main import app
from app.models.behavioral_intelligence import (
    BehavioralAnomaly,
    BehavioralAnomalyType,
    BehavioralIntelligenceResult,
    TransmissionGap,
    VesselBehavioralProfile,
)
from app.models.common import AssetType
from app.models.drift import DriftResult, DriftStep
from app.models.evidence_fusion import (
    EvidenceFusionResult,
    SignalStatus,
    VesselFusedEvidence,
)
from app.models.satellite import SpillDetection
from app.models.source_estimation import BackwardDriftStep, SourceEstimateResult
from app.models.trajectory_analysis import (
    CenterlineProximityProfile,
    TrajectoryAnalysisResult,
    TrajectoryQualityProfile,
    VesselTrajectoryAnalysis,
    ZoneTransitProfile,
)
from app.models.vessel import (
    CandidateGenerationStatus,
    CandidateVessel,
    CandidateVesselGenerationResult,
    VesselPosition,
)
from app.services.candidate_environment import (
    CandidateEnvironment,
    CurrentEvidence,
    EnvironmentalDataQuality,
    WindEvidence,
    WindRegime,
    WindRegimeAssessment,
)
from app.services.candidate_ranking import rank_candidates
from app.services.evidence_fusion import (
    EvidenceFusionError,
    _NOMINAL_WEIGHTS,
    _score_spatial,
    _score_temporal,
    _score_trajectory,
    export_evidence_fusion_geojson,
    fuse_evidence,
)

_UTC = timezone.utc


class StageF1EvidenceFusionTests(unittest.TestCase):
    """Hermetic tests covering all 20 Stage F1 scientific requirements."""

    def setUp(self) -> None:
        self.t_source = datetime(2018, 10, 9, 12, 45, 33, tzinfo=_UTC)
        self.t_spill = datetime(2018, 10, 9, 13, 0, 0, tzinfo=_UTC)
        self.inv_id = "inv-f1-audit"
        self.spill_id = "spill-f1-audit"
        self.r_zone_km = 8.0
        self.center_lon = 9.47
        self.center_lat = 43.24

        # D3 source estimate
        self.source_estimate = SourceEstimateResult(
            id="src-est-001",
            investigation_id=self.inv_id,
            spill_detection_id=self.spill_id,
            wind_asset_id="wind-asset-001",
            current_asset_id="current-asset-001",
            asset_id="drift-product-001",
            step_hours=1.0,
            lookback_hours=6.0,
            leeway_fraction=0.035,
            observation_time=self.t_spill,
            origin_lon=self.center_lon + 0.05,
            origin_lat=self.center_lat - 0.05,
            origin_time=self.t_spill,
            source_point_lon=self.center_lon,
            source_point_lat=self.center_lat,
            source_time=self.t_source,
            source_uncertainty_radius_km=self.r_zone_km,
            source_zone_geometry={
                "type": "Polygon",
                "coordinates": [[
                    [self.center_lon - 0.1, self.center_lat - 0.1],
                    [self.center_lon + 0.1, self.center_lat - 0.1],
                    [self.center_lon + 0.1, self.center_lat + 0.1],
                    [self.center_lon - 0.1, self.center_lat + 0.1],
                    [self.center_lon - 0.1, self.center_lat - 0.1],
                ]],
            },
            steps=[
                BackwardDriftStep(
                    timestamp=self.t_spill,
                    lon=self.center_lon + 0.05,
                    lat=self.center_lat - 0.05,
                    u_wind_ms=2.0,
                    v_wind_ms=3.0,
                    u_current_ms=0.1,
                    v_current_ms=-0.1,
                    drift_u_ms=0.15,
                    drift_v_ms=0.10,
                    cumulative_backward_distance_m=2500.0,
                    uncertainty_radius_m=2500.0,
                ),
                BackwardDriftStep(
                    timestamp=self.t_source,
                    lon=self.center_lon,
                    lat=self.center_lat,
                    u_wind_ms=2.0,
                    v_wind_ms=3.0,
                    u_current_ms=0.1,
                    v_current_ms=-0.1,
                    drift_u_ms=0.15,
                    drift_v_ms=0.10,
                    cumulative_backward_distance_m=5000.0,
                    uncertainty_radius_m=5000.0,
                ),
            ],
            metadata={"status": "completed", "current_fallback": False, "shoreline_terminated": False},
        )

        # B3 spill detection
        self.spill_detection = SpillDetection(
            id=self.spill_id,
            investigation_id=self.inv_id,
            asset_id="asset-sar-spill",
            scene_id="S1A_IW_GRDH_20181009",
            detected=True,
            confidence=0.88,
            geometry={"type": "Point", "coordinates": [self.center_lon, self.center_lat]},
            area=1250000.0,
            metadata={
                "maritime_domain_status": "HIGH_SEAS",
                "vv_vh_ratio": 6.4,
                "provenance": {"detector": "adaptive_threshold_v2"},
            },
        )

        # C2 candidate environmental context
        self.env_context = CandidateEnvironment(
            wind=WindEvidence(
                speed_ms=5.5,
                direction_from_deg=220.0,
                source="ERA5",
                source_timestamp="2018-10-09T12:00:00Z",
                spatial_interpolation="bilinear",
            ),
            current=CurrentEvidence(
                speed_ms=0.18,
                direction_to_deg=45.0,
                source="CMEMS_GLORYS12V1",
                source_timestamp="2018-10-09T12:00:00Z",
                spatial_interpolation="nearest_neighbor",
            ),
            regime=WindRegimeAssessment(
                classification=WindRegime.FAVORABLE_DETECTION_WINDOW,
                evidence_text="Active Bragg scattering supports damping contrast.",
                lookalike_risk="LOW_LOOKALIKE_PROBABILITY",
                damping_consistent=True,
            ),
            data_quality=EnvironmentalDataQuality(
                wind_available=True,
                current_available=True,
                interpolation_valid=True,
                outside_coverage=False,
            ),
        )

        # E3 behavioral intelligence
        self.behavioral_result = BehavioralIntelligenceResult(
            id="beh-res-001",
            investigation_id=self.inv_id,
            spill_detection_id=self.spill_id,
            source_estimate_id=self.source_estimate.id,
            candidate_generation_id="cand-gen-001",
            analyzed_vessel_count=1,
            total_anomalies_detected=1,
            total_transmission_gaps_detected=1,
            profiles=[
                VesselBehavioralProfile(
                    candidate_id="cand-001",
                    vessel_id="vessel-001",
                    mmsi="111222333",
                    vessel_name="Alpha",
                    anomalies=[
                        BehavioralAnomaly(
                            anomaly_type=BehavioralAnomalyType.SPEED_DROP_IN_ZONE,
                            timestamp=self.t_source,
                            location_lon=self.center_lon,
                            location_lat=self.center_lat,
                            inside_source_zone=True,
                            description="Speed reduction observed near center.",
                        )
                    ],
                    transmission_gaps=[
                        TransmissionGap(
                            gap_start_time=self.t_source - timedelta(minutes=45),
                            gap_end_time=self.t_source,
                            gap_duration_seconds=2700.0,
                            gap_start_lon=self.center_lon,
                            gap_start_lat=self.center_lat,
                            gap_end_lon=self.center_lon + 0.05,
                            gap_end_lat=self.center_lat + 0.05,
                            distance_across_gap_km=5.0,
                            spanned_source_zone=True,
                        )
                    ],
                    loitering_detected=False,
                )
            ],
        )

    def _make_candidate(
        self,
        candidate_id: str = "cand-001",
        mmsi: str = "111222333",
        vessel_name: str = "Alpha",
        inside_zone: bool = True,
        d_center_km: float = 1.5,
        d_boundary_km: float = 0.0,
        time_offset_h: float = 0.25,
        obs_count: int = 4,
        is_real: bool = False,
        data_source_type: str = "curated_historical_reconstruction",
    ) -> tuple[CandidateVessel, VesselTrajectoryAnalysis]:
        t_cpa = self.t_source + timedelta(hours=time_offset_h)
        positions = [
            VesselPosition(
                timestamp=t_cpa + timedelta(minutes=i * 5),
                lon=self.center_lon + i * 0.005,
                lat=self.center_lat + i * 0.005,
                speed=10.0,
                course=210.0,
            )
            for i in range(obs_count)
        ]

        cand = CandidateVessel(
            candidate_id=candidate_id,
            vessel_id=f"vessel-{candidate_id}",
            mmsi=mmsi,
            vessel_name=vessel_name,
            vessel_type="Tanker",
            closest_position_lon=self.center_lon,
            closest_position_lat=self.center_lat,
            closest_position_time=t_cpa,
            inside_source_zone=inside_zone,
            min_distance_to_source_center_km=d_center_km,
            distance_to_zone_boundary_km=d_boundary_km,
            time_offset_from_source_hours=time_offset_h,
            speed_over_ground=10.0,
            course_over_ground=210.0,
            observed_positions_count=obs_count,
            raw_positions=positions,
            metadata={
                "imo": "9876543",
                "call_sign": "ABCD",
                "flag": "PA",
                "data_source_type": data_source_type,
                "is_real_observation": is_real,
            },
        )

        traj = VesselTrajectoryAnalysis(
            candidate_id=candidate_id,
            vessel_id=f"vessel-{candidate_id}",
            mmsi=mmsi,
            vessel_name=vessel_name,
            vessel_type="Tanker",
            observed_positions_count=obs_count,
            transit_profile=ZoneTransitProfile(
                points_inside_count=obs_count if inside_zone else 0,
                min_distance_to_center_km=d_center_km,
                distance_to_zone_boundary_km=d_boundary_km,
                closest_position_time=t_cpa,
                time_offset_from_source_hours=time_offset_h,
                cpa_lon=self.center_lon,
                cpa_lat=self.center_lat,
            ),
            centerline_proximity=CenterlineProximityProfile(
                min_distance_to_centerline_km=0.8,
                closest_centerline_point_lon=self.center_lon,
                closest_centerline_point_lat=self.center_lat,
                course_drift_angle_diff_deg=15.0,
            ),
            quality=TrajectoryQualityProfile(
                observation_count=obs_count,
                duplicate_count=0,
                temporal_span_seconds=float(obs_count * 300),
                has_multiple_observations=(obs_count >= 2),
                has_valid_kinematics=(obs_count >= 2),
                sparse_track=(obs_count <= 2),
                is_real_observation=is_real,
                data_source_type=data_source_type,
                quality_flags=["sparse_track"] if obs_count <= 2 else [],
            ),
            evidence={
                "kinematic": {
                    "reported_sog_knots": 10.0,
                    "derived_speed_mps": 5.14,
                    "bearing_degrees": 210.0,
                    "trajectory_continuity": "continuous",
                    "has_valid_kinematics": (obs_count >= 2),
                    "invalid_intervals_count": 0,
                }
            },
        )
        return cand, traj

    def _make_candidate_result(self, candidates: list[CandidateVessel]) -> CandidateVesselGenerationResult:
        return CandidateVesselGenerationResult(
            id="cg-001",
            investigation_id=self.inv_id,
            spill_detection_id=self.spill_id,
            source_estimate_id=self.source_estimate.id,
            status=CandidateGenerationStatus.COMPLETED,
            source_time=self.t_source,
            source_uncertainty_radius_km=self.r_zone_km,
            temporal_window_start=self.t_source - timedelta(hours=6),
            temporal_window_end=self.t_source + timedelta(hours=6),
            spatial_query_bbox={"west": 9.0, "south": 43.0, "east": 10.0, "north": 43.5},
            candidate_count=len(candidates),
            total_vessels_checked=len(candidates),
            candidates=candidates,
        )

    def _make_trajectory_result(self, analyses: list[VesselTrajectoryAnalysis]) -> TrajectoryAnalysisResult:
        return TrajectoryAnalysisResult(
            id="tr-001",
            investigation_id=self.inv_id,
            spill_detection_id=self.spill_id,
            source_estimate_id=self.source_estimate.id,
            candidate_generation_id="cg-001",
            analyzed_vessel_count=len(analyses),
            analyses=analyses,
            metadata={"zero_fabrication": True},
        )

    # -----------------------------------------------------------------------
    # Requirement 1: All Evidence Dimensions Present
    # -----------------------------------------------------------------------
    def test_01_all_evidence_dimensions_present(self) -> None:
        cand, traj = self._make_candidate()
        cr = self._make_candidate_result([cand])
        tr = self._make_trajectory_result([traj])

        with tempfile.TemporaryDirectory() as tmpdir:
            result, asset = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
                spill_detection=self.spill_detection, environmental_context=self.env_context,
            )

        self.assertEqual(result.candidate_count, 1)
        fev = result.fused_candidates[0]
        ev = fev.evidence

        # Check all 8 sub-blocks exist
        self.assertIsNotNone(ev.spill.spill_id)
        self.assertIsNotNone(ev.environment.wind_speed_ms)
        self.assertIsNotNone(ev.source.source_point_lon)
        self.assertIsNotNone(ev.ais.mmsi)
        self.assertIsNotNone(ev.spatial.min_distance_to_center_km)
        self.assertIsNotNone(ev.temporal.time_offset_from_source_hours)
        self.assertIsNotNone(ev.kinematic.reported_sog_knots)
        self.assertIsNotNone(ev.data_quality.observed_positions_count)

        # Availability
        self.assertIn("spatial_proximity", fev.availability.available_dimensions)
        self.assertIn("temporal_proximity", fev.availability.available_dimensions)
        self.assertIn("trajectory_consistency", fev.availability.available_dimensions)
        self.assertIn("environmental_context", fev.availability.available_dimensions)
        self.assertIn("spill_detection", fev.availability.available_dimensions)

    # -----------------------------------------------------------------------
    # Requirement 2: Missing Environmental Data
    # -----------------------------------------------------------------------
    def test_02_missing_environmental_data(self) -> None:
        cand, traj = self._make_candidate()
        cr = self._make_candidate_result([cand])
        tr = self._make_trajectory_result([traj])

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
                environmental_context=None,
            )

        fev = result.fused_candidates[0]
        self.assertEqual(fev.evidence.environment.missing_data_status, "UNAVAILABLE")
        self.assertIn("environmental_context", fev.availability.unavailable_dimensions)
        self.assertIn("environmental_context", fev.availability.missing_reasons)
        # Primary concordance score is STILL computed from valid spatial/temporal/trajectory channels
        self.assertIsNotNone(fev.composite_concordance_score)

    # -----------------------------------------------------------------------
    # Requirement 3: Missing AIS Data (0 observations)
    # -----------------------------------------------------------------------
    def test_03_missing_ais_data(self) -> None:
        cand, traj = self._make_candidate(obs_count=0)
        cand.raw_positions = []
        traj.observed_positions_count = 0
        cr = self._make_candidate_result([cand])
        tr = self._make_trajectory_result([traj])

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        fev = result.fused_candidates[0]
        # Trajectory consistency channel is INSUFFICIENT_DATA
        traj_sig = next(s for s in fev.primary_signals if s.channel_name == "trajectory_consistency")
        self.assertEqual(traj_sig.status, SignalStatus.INSUFFICIENT_DATA)
        self.assertIsNone(traj_sig.score)
        # Dynamic renormalization: score uses remaining valid channels
        self.assertIsNotNone(fev.composite_concordance_score)
        self.assertEqual(fev.normalized_evidence.trajectory_quality, "SPARSE")

    # -----------------------------------------------------------------------
    # Requirement 4: Single-Observation AIS
    # -----------------------------------------------------------------------
    def test_04_single_observation_ais(self) -> None:
        cand, traj = self._make_candidate(obs_count=1)
        cr = self._make_candidate_result([cand])
        tr = self._make_trajectory_result([traj])

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        fev = result.fused_candidates[0]
        self.assertTrue(fev.evidence.data_quality.sparse_track)
        self.assertEqual(fev.normalized_evidence.trajectory_quality, "SPARSE")
        self.assertTrue(any("Single AIS observation ping" in w for w in fev.warnings))

    # -----------------------------------------------------------------------
    # Requirement 5: Sparse Trajectory (2 observations)
    # -----------------------------------------------------------------------
    def test_05_sparse_trajectory_two_pings(self) -> None:
        cand, traj = self._make_candidate(obs_count=2)
        cr = self._make_candidate_result([cand])
        tr = self._make_trajectory_result([traj])

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        fev = result.fused_candidates[0]
        self.assertEqual(fev.evidence.data_quality.observed_positions_count, 2)
        self.assertTrue(fev.evidence.data_quality.sparse_track)

    # -----------------------------------------------------------------------
    # Requirement 6: D3 Current Fallback
    # -----------------------------------------------------------------------
    def test_06_d3_current_fallback_flag_preserved(self) -> None:
        source_fallback = self.source_estimate.model_copy(deep=True)
        source_fallback.metadata = {
            "status": "completed",
            "current_fallback": True,
            "fallback_info": "CMEMS current unavailable; wind-only drift advection used",
        }
        cand, traj = self._make_candidate()
        cr = self._make_candidate_result([cand])
        tr = self._make_trajectory_result([traj])

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                self.inv_id, self.spill_id, source_fallback, cr, tr, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        fev = result.fused_candidates[0]
        self.assertTrue(fev.evidence.source.current_fallback_applied)
        self.assertTrue(fev.uncertainty.source_fallback_applied)
        self.assertTrue(any("wind-only current fallback" in w for w in fev.warnings))
        self.assertTrue(any("wind-only current fallback" in w for w in result.warnings))

    # -----------------------------------------------------------------------
    # Requirement 7: D3 Shoreline Termination
    # -----------------------------------------------------------------------
    def test_07_d3_shoreline_termination(self) -> None:
        source_shore = self.source_estimate.model_copy(deep=True)
        source_shore.metadata = {
            "status": "terminated_at_shoreline",
            "shoreline_terminated": True,
            "termination_reason": "shoreline_boundary_reached",
        }
        cand, traj = self._make_candidate()
        cr = self._make_candidate_result([cand])
        tr = self._make_trajectory_result([traj])

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                self.inv_id, self.spill_id, source_shore, cr, tr, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        fev = result.fused_candidates[0]
        self.assertTrue(fev.evidence.source.shoreline_terminated)
        self.assertTrue(fev.uncertainty.source_fallback_applied)
        self.assertTrue(any("shoreline boundary" in w for w in fev.warnings))

    # -----------------------------------------------------------------------
    # Requirement 8: Source Uncertainty Scaling
    # -----------------------------------------------------------------------
    def test_08_source_uncertainty_scaling(self) -> None:
        # Candidate at distance 4.0 km with R_zone=4.0 km vs R_zone=8.0 km
        s_tight = _score_spatial(d_center_km=4.0, d_boundary_km=0.0, r_zone_km=4.0)
        s_wide = _score_spatial(d_center_km=4.0, d_boundary_km=0.0, r_zone_km=8.0)

        # Under wider uncertainty, same physical distance has higher compatibility
        self.assertAlmostEqual(s_tight, math.exp(-0.5 * (4.0 / 4.0) ** 2), places=5)
        self.assertAlmostEqual(s_wide, math.exp(-0.5 * (4.0 / 8.0) ** 2), places=5)
        self.assertGreater(s_wide, s_tight)

    # -----------------------------------------------------------------------
    # Requirement 9: Spatial Normalization Function
    # -----------------------------------------------------------------------
    def test_09_spatial_normalization_math(self) -> None:
        # At exact center (d_center=0, d_boundary=0) -> S=1.0
        self.assertAlmostEqual(_score_spatial(0.0, 0.0, 8.0), 1.0, places=6)

        # At boundary (d_center=8.0, d_boundary=0.0) -> S=exp(-0.5) ≈ 0.60653
        boundary_val = math.exp(-0.5)
        self.assertAlmostEqual(_score_spatial(8.0, 0.0, 8.0), boundary_val, places=5)

        # Outside boundary (d_boundary=8.0, r_zone=8.0) -> S=exp(-0.5)*exp(-1) ≈ 0.22313
        outside_val = math.exp(-0.5) * math.exp(-1.0)
        self.assertAlmostEqual(_score_spatial(16.0, 8.0, 8.0), outside_val, places=5)

    # -----------------------------------------------------------------------
    # Requirement 10: Temporal Normalization Function
    # -----------------------------------------------------------------------
    def test_10_temporal_normalization_math(self) -> None:
        # At exact source time (dt=0) -> S=1.0
        self.assertAlmostEqual(_score_temporal(0.0, 2.0), 1.0, places=6)

        # At dt=tau_scale (dt=2.0, tau=2.0) -> S=exp(-0.5) ≈ 0.60653
        self.assertAlmostEqual(_score_temporal(2.0, 2.0), math.exp(-0.5), places=5)

        # At dt=2*tau_scale (dt=4.0, tau=2.0) -> S=exp(-0.5 * 4) = exp(-2.0) ≈ 0.13534
        self.assertAlmostEqual(_score_temporal(4.0, 2.0), math.exp(-2.0), places=5)

    # -----------------------------------------------------------------------
    # Requirement 11: Trajectory Alignment Normalization Function
    # -----------------------------------------------------------------------
    def test_11_trajectory_alignment_math(self) -> None:
        # On centerline (d_cl=0), perfectly aligned (ang=0) -> 0.6*1.0 + 0.4*1.0 = 1.0
        s_align, _ = _score_trajectory(d_centerline_km=0.0, r_zone_km=8.0, angular_diff_deg=0.0)
        self.assertAlmostEqual(s_align, 1.0, places=6)

        # On centerline (d_cl=0), perpendicular (ang=90) -> 0.6*1.0 + 0.4*0.5 = 0.80
        s_perp, _ = _score_trajectory(d_centerline_km=0.0, r_zone_km=8.0, angular_diff_deg=90.0)
        self.assertAlmostEqual(s_perp, 0.80, places=6)

        # On centerline (d_cl=0), opposite direction (ang=180) -> 0.6*1.0 + 0.4*0.0 = 0.60
        s_opp, _ = _score_trajectory(d_centerline_km=0.0, r_zone_km=8.0, angular_diff_deg=180.0)
        self.assertAlmostEqual(s_opp, 0.60, places=6)

    # -----------------------------------------------------------------------
    # Requirement 12: Source-Zone Membership
    # -----------------------------------------------------------------------
    def test_12_source_zone_membership(self) -> None:
        cand_in, traj_in = self._make_candidate(inside_zone=True, d_boundary_km=0.0)
        cand_out, traj_out = self._make_candidate(
            candidate_id="cand-out", mmsi="999888777", inside_zone=False, d_boundary_km=3.5
        )

        cr = self._make_candidate_result([cand_in, cand_out])
        tr = self._make_trajectory_result([traj_in, traj_out])

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        self.assertTrue(result.fused_candidates[0].evidence.spatial.inside_source_zone)
        self.assertTrue(result.fused_candidates[0].normalized_evidence.source_zone_membership)

        self.assertFalse(result.fused_candidates[1].evidence.spatial.inside_source_zone)
        self.assertFalse(result.fused_candidates[1].normalized_evidence.source_zone_membership)

    # -----------------------------------------------------------------------
    # Requirement 13: Evidence Provenance (Zero-Fabrication & Benchmark Flag)
    # -----------------------------------------------------------------------
    def test_13_evidence_provenance(self) -> None:
        cand, traj = self._make_candidate(is_real=False, data_source_type="curated_historical_reconstruction")
        cr = self._make_candidate_result([cand])
        tr = self._make_trajectory_result([traj])

        with tempfile.TemporaryDirectory() as tmpdir:
            result, asset = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        fev = result.fused_candidates[0]
        # Benchmark provenance must be explicit
        self.assertFalse(fev.provenance.is_real_observation)
        self.assertEqual(fev.provenance.data_source_type, "curated_historical_reconstruction")
        self.assertTrue(fev.metadata["zero_fabrication"])
        self.assertFalse(asset.provenance.extra["is_real_observation"])
        self.assertEqual(asset.provenance.extra["data_source_type"], "curated_historical_reconstruction")

    # -----------------------------------------------------------------------
    # Requirement 14: No Double-Counting (Forward Drift and Behavioral Invariance)
    # -----------------------------------------------------------------------
    def test_14_no_double_counting(self) -> None:
        cand, traj = self._make_candidate()
        cr = self._make_candidate_result([cand])
        tr = self._make_trajectory_result([traj])

        # Baseline without D1 forward drift
        with tempfile.TemporaryDirectory() as tmpdir1:
            res_no_drift, _ = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr, self.behavioral_result,
                drift_result=None, output_dir=tmpdir1, registry=InMemoryAssetRegistry(),
            )

        # With D1 forward drift
        d1 = DriftResult(
            id="drift-001",
            investigation_id=self.inv_id,
            spill_detection_id=self.spill_id,
            wind_asset_id="wind-001",
            current_asset_id="curr-001",
            asset_id="drift-asset-001",
            observation_time=self.t_spill,
            origin_lon=self.center_lon,
            origin_lat=self.center_lat,
            total_duration_hours=6.0,
            step_hours=1.0,
            leeway_fraction=0.03,
            endpoint_lon=self.center_lon,
            endpoint_lat=self.center_lat,
            model_name="trajectory",
            run_time=self.t_spill,
            steps=[
                DriftStep(
                    timestamp=self.t_spill,
                    lon=self.center_lon,
                    lat=self.center_lat,
                    u_wind_ms=5.0,
                    v_wind_ms=0.0,
                    u_current_ms=0.2,
                    v_current_ms=0.0,
                    drift_u_ms=0.35,
                    drift_v_ms=0.0,
                    cumulative_distance_m=100.0,
                )
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir2:
            res_with_drift, _ = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr, self.behavioral_result,
                drift_result=d1, output_dir=tmpdir2, registry=InMemoryAssetRegistry(),
            )

        # Invariant: Score must be exactly bit-for-bit identical
        s1 = res_no_drift.fused_candidates[0].composite_concordance_score
        s2 = res_with_drift.fused_candidates[0].composite_concordance_score
        self.assertEqual(s1, s2)
        self.assertEqual(res_with_drift.metadata["behavioral_contribution_to_score"], 0.0)
        self.assertEqual(res_with_drift.metadata["forward_drift_contribution_to_score"], 0.0)

    # -----------------------------------------------------------------------
    # Requirement 15: Unavailable vs Negative Evidence
    # -----------------------------------------------------------------------
    def test_15_unavailable_vs_negative_evidence(self) -> None:
        cand, traj = self._make_candidate()
        cr = self._make_candidate_result([cand])
        tr_empty = self._make_trajectory_result([])

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr_empty, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        fev = result.fused_candidates[0]
        # Missing channel has status=INSUFFICIENT_DATA and score=None (NOT 0.0)
        for sig in fev.primary_signals:
            self.assertEqual(sig.status, SignalStatus.INSUFFICIENT_DATA)
            self.assertIsNone(sig.score)
        # Concordance score is None, NOT 0.0
        self.assertIsNone(fev.composite_concordance_score)
        self.assertEqual(fev.evidence_availability_ratio, 0.0)

    # -----------------------------------------------------------------------
    # Requirement 16: Ulysse Reference Benchmark
    # -----------------------------------------------------------------------
    def test_16_benchmark_ulysse(self) -> None:
        # Reference Ulysse and Virginia data
        t_cpa = self.t_source + timedelta(hours=0.7592)
        p_u = VesselPosition(timestamp=t_cpa, lon=9.478, lat=43.248, speed=0.0, course=205.0)
        p_v = VesselPosition(timestamp=t_cpa, lon=9.478, lat=43.248, speed=0.0, course=90.0)

        cand_u = CandidateVessel(
            candidate_id="cand-228308800", vessel_id="228308800", mmsi="228308800",
            vessel_name="MV ULYSSE", vessel_type="Ro-Ro Cargo",
            closest_position_lon=9.478, closest_position_lat=43.248,
            closest_position_time=t_cpa, inside_source_zone=False,
            min_distance_to_source_center_km=3.884, distance_to_zone_boundary_km=0.384,
            time_offset_from_source_hours=0.7592, speed_over_ground=0.0, course_over_ground=205.0,
            observed_positions_count=1, raw_positions=[p_u],
            metadata={"data_source_type": "curated_historical_reconstruction", "is_real_observation": False},
        )
        cand_v = CandidateVessel(
            candidate_id="cand-229986000", vessel_id="229986000", mmsi="229986000",
            vessel_name="CSL VIRGINIA", vessel_type="Container Ship",
            closest_position_lon=9.478, closest_position_lat=43.248,
            closest_position_time=t_cpa, inside_source_zone=False,
            min_distance_to_source_center_km=3.884, distance_to_zone_boundary_km=0.384,
            time_offset_from_source_hours=0.7592, speed_over_ground=0.0, course_over_ground=90.0,
            observed_positions_count=1, raw_positions=[p_v],
            metadata={"data_source_type": "curated_historical_reconstruction", "is_real_observation": False},
        )

        traj_u = VesselTrajectoryAnalysis(
            candidate_id="cand-228308800", vessel_id="228308800", mmsi="228308800",
            vessel_name="MV ULYSSE", observed_positions_count=1,
            transit_profile=ZoneTransitProfile(
                points_inside_count=0, min_distance_to_center_km=3.884,
                distance_to_zone_boundary_km=0.384, closest_position_time=t_cpa,
                time_offset_from_source_hours=0.7592, cpa_lon=9.478, cpa_lat=43.248,
            ),
            centerline_proximity=CenterlineProximityProfile(
                min_distance_to_centerline_km=0.55,
                closest_centerline_point_lon=9.4845, closest_centerline_point_lat=43.2465,
                course_drift_angle_diff_deg=None,  # Stationary or no valid COG
            ),
            quality=TrajectoryQualityProfile(
                observation_count=1, duplicate_count=0, temporal_span_seconds=0.0,
                has_multiple_observations=False, has_valid_kinematics=False,
                sparse_track=True, is_real_observation=False,
                data_source_type="curated_historical_reconstruction",
            ),
        )
        traj_v = VesselTrajectoryAnalysis(
            candidate_id="cand-229986000", vessel_id="229986000", mmsi="229986000",
            vessel_name="CSL VIRGINIA", observed_positions_count=1,
            transit_profile=ZoneTransitProfile(
                points_inside_count=0, min_distance_to_center_km=3.884,
                distance_to_zone_boundary_km=0.384, closest_position_time=t_cpa,
                time_offset_from_source_hours=0.7592, cpa_lon=9.478, cpa_lat=43.248,
            ),
            centerline_proximity=CenterlineProximityProfile(
                min_distance_to_centerline_km=0.55,
                closest_centerline_point_lon=9.4845, closest_centerline_point_lat=43.2465,
                course_drift_angle_diff_deg=None,
            ),
            quality=TrajectoryQualityProfile(
                observation_count=1, duplicate_count=0, temporal_span_seconds=0.0,
                has_multiple_observations=False, has_valid_kinematics=False,
                sparse_track=True, is_real_observation=False,
                data_source_type="curated_historical_reconstruction",
            ),
        )

        cr = self._make_candidate_result([cand_u, cand_v])
        tr = self._make_trajectory_result([traj_u, traj_v])

        with tempfile.TemporaryDirectory() as tmpdir:
            result, asset = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        self.assertEqual(result.candidate_count, 2)
        # Order is preserved: cand_u at index 0, cand_v at index 1
        self.assertEqual(result.fused_candidates[0].mmsi, "228308800")
        self.assertEqual(result.fused_candidates[1].mmsi, "229986000")

        # Provenance: both marked false for real observation
        self.assertFalse(result.fused_candidates[0].provenance.is_real_observation)
        self.assertFalse(result.fused_candidates[1].provenance.is_real_observation)

        # Physical metrics
        u_fev = result.fused_candidates[0]
        self.assertAlmostEqual(u_fev.evidence.spatial.min_distance_to_center_km, 3.884, places=2)
        self.assertAlmostEqual(u_fev.evidence.spatial.min_distance_to_centerline_km, 0.55, places=2)
        self.assertAlmostEqual(u_fev.evidence.temporal.time_offset_from_source_hours, 0.7592, places=3)
        self.assertGreater(u_fev.composite_concordance_score, 0.60)

    # -----------------------------------------------------------------------
    # Requirement 17: E2 -> F1 Serialization Round-Trip
    # -----------------------------------------------------------------------
    def test_17_e2_to_f1_serialization(self) -> None:
        cand, traj = self._make_candidate()
        cr = self._make_candidate_result([cand])
        tr = self._make_trajectory_result([traj])

        with tempfile.TemporaryDirectory() as tmpdir:
            result, asset = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )
            # Verify GeoJSON file is readable and valid
            geojson_path = Path(asset.location)
            self.assertTrue(geojson_path.exists())
            data = json.loads(geojson_path.read_text(encoding="utf-8"))
            self.assertEqual(data["type"], "FeatureCollection")
            self.assertTrue(any(f["properties"].get("feature_kind") == "evidence_fusion_candidate" for f in data["features"]))

            # Verify Pydantic JSON serialization round-trip
            json_dump = result.model_dump_json()
            restored = EvidenceFusionResult.model_validate_json(json_dump)
            self.assertEqual(restored.id, result.id)
            self.assertEqual(restored.candidate_count, result.candidate_count)
            self.assertEqual(
                restored.fused_candidates[0].composite_concordance_score,
                result.fused_candidates[0].composite_concordance_score,
            )

    # -----------------------------------------------------------------------
    # Requirement 18: Backward Compatibility with Stage F2
    # -----------------------------------------------------------------------
    def test_18_backward_compatibility_with_stage_f2(self) -> None:
        cand1, traj1 = self._make_candidate("c-1", "111", "VesselA", inside_zone=True, d_center_km=0.5, time_offset_h=0.1)
        cand2, traj2 = self._make_candidate("c-2", "222", "VesselB", inside_zone=False, d_center_km=5.0, time_offset_h=1.5)

        cr = self._make_candidate_result([cand1, cand2])
        tr = self._make_trajectory_result([traj1, traj2])

        with tempfile.TemporaryDirectory() as tmpdir:
            f1_result, _ = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

            # Pass F1 result directly into Stage F2
            ranking, rank_asset = rank_candidates(
                f1_result,
                investigation_id=self.inv_id,
                spill_id=self.spill_id,
                output_dir=tmpdir,
                registry=InMemoryAssetRegistry(),
            )

        self.assertEqual(ranking.candidate_count, 2)
        # VesselA with higher concordance is ranked #1 by F2
        self.assertEqual(ranking.ranked_candidates[0].candidate_id, "c-1")
        self.assertEqual(ranking.ranked_candidates[0].rank, 1)

    # -----------------------------------------------------------------------
    # Requirement 19: Deterministic Output
    # -----------------------------------------------------------------------
    def test_19_deterministic_output(self) -> None:
        cand, traj = self._make_candidate()
        cr = self._make_candidate_result([cand])
        tr = self._make_trajectory_result([traj])

        with tempfile.TemporaryDirectory() as tmpdir:
            res1, _ = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )
            res2, _ = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        self.assertEqual(
            res1.fused_candidates[0].composite_concordance_score,
            res2.fused_candidates[0].composite_concordance_score,
        )
        self.assertEqual(
            res1.fused_candidates[0].evidence.model_dump(),
            res2.fused_candidates[0].evidence.model_dump(),
        )

    # -----------------------------------------------------------------------
    # Requirement 20: No Responsibility Probability Generated
    # -----------------------------------------------------------------------
    def test_20_no_responsibility_probability_generated(self) -> None:
        cand, traj = self._make_candidate(inside_zone=True, d_center_km=0.01, time_offset_h=0.0)
        cr = self._make_candidate_result([cand])
        tr = self._make_trajectory_result([traj])

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                self.inv_id, self.spill_id, self.source_estimate, cr, tr, self.behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        dump = result.model_dump_json()

        # Prohibited legal/attribution vocabulary
        for prohibited in [
            "guilty_vessel",
            "culprit",
            "perpetrator",
            "probability_of_guilt",
            "probability_of_spill",
            "probability_of_responsibility",
            "definitely_caused",
        ]:
            self.assertNotIn(prohibited, dump.lower())

        # Score interpretation disclaimer must be present
        self.assertIn("not a probability", result.metadata["score_interpretation"].lower())
        self.assertIn("not a calibrated statistical probability", result.fused_candidates[0].evidence.spill.caveat.lower())


if __name__ == "__main__":
    unittest.main()
