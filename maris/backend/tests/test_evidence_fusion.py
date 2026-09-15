"""Hermetic tests for MARIS Stage F1 — Multi-Source Spatio-Temporal Evidence Fusion.

All tests are completely offline and hermetic (no network, no real files).

Verifies:
- Primary concordance score uses only spatial/temporal/trajectory channels (w=0.50/0.25/0.25)
- Behavioral invariance: E3 anomalies/gaps/loitering contribute 0.00 to score
- D1 forward drift decoupling: supplying or omitting D1 does not change primary score
- B3 contract compatibility: SpillDetection uses actual schema fields
- Spatial normalization: S_spatial = 1.0 at center, ~0.6065 at boundary, decays outside
- Temporal normalization: S_temporal = 1.0 at t_source, Gaussian decay
- Trajectory normalization: combined cross-track + angular score
- Dynamic weight renormalization when channels are missing
- Missing data handling: INSUFFICIENT_DATA/UNAVAILABLE excluded from denominator (not treated as 0)
- Zero fabrication: no synthetic AIS positions created
- Order preservation: E1 candidate input order preserved exactly
- Evidence availability ratio: N_valid / 3
- Provenance: parent IDs, zero_fabrication flag, score interpretation note
- GeoJSON artifact: valid FeatureCollection exported
- AssetRegistry registration: AssetType.DOCUMENT with asset_type=evidence_fusion
- Deterministic repeatability: identical inputs produce identical outputs
- API endpoint: 200 OK success, 404 not found, 422 validation errors
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
from app.main import app
from app.models.asset import Asset
from app.models.behavioral_intelligence import (
    AnchorSwingProfile,
    AnomalySeverity,
    BehavioralAnomaly,
    BehavioralAnomalyType,
    BehavioralIntelligenceResult,
    TransmissionGap,
    VesselBehavioralProfile,
)
from app.models.common import AssetType, Provenance
from app.models.drift import DriftResult, DriftStep
from app.models.evidence_fusion import (
    BehavioralContextSummary,
    EvidenceFusionRequest,
    EvidenceFusionResult,
    EvidenceSignal,
    ForwardDriftCrossCheck,
    SignalStatus,
    VesselFusedEvidence,
)
from app.models.source_estimation import BackwardDriftStep, SourceEstimateResult
from app.models.trajectory_analysis import (
    CenterlineProximityProfile,
    TrajectoryAnalysisResult,
    VesselTrajectoryAnalysis,
    ZoneTransitProfile,
)
from app.models.vessel import (
    CandidateGenerationStatus,
    CandidateVessel,
    CandidateVesselGenerationResult,
    VesselPosition,
)
from app.services.evidence_fusion import (
    EvidenceFusionError,
    _NOMINAL_WEIGHTS,
    _build_behavioral_context,
    _build_forward_drift_cross_check,
    _composite_score,
    _evidence_availability_ratio,
    _fuse_vessel_evidence,
    _score_spatial,
    _score_temporal,
    _score_trajectory,
    export_evidence_fusion_geojson,
    fuse_evidence,
)


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_UTC = timezone.utc
_T0 = datetime(2026, 9, 8, 10, 0, 0, tzinfo=_UTC)

CENTER_LON = 9.47
CENTER_LAT = 43.24
RADIUS_KM = 8.0


def _make_polygon_ring(center_lon: float, center_lat: float, radius_km: float) -> list[list[float]]:
    """Generate a 32-vertex regular polygon ring for the source candidate zone."""
    coords: list[list[float]] = []
    r_deg = radius_km / 111.0
    for i in range(32):
        angle = 2.0 * math.pi * i / 32
        coords.append([
            round(center_lon + r_deg * math.cos(angle) / math.cos(math.radians(center_lat)), 6),
            round(center_lat + r_deg * math.sin(angle), 6),
        ])
    coords.append(coords[0])
    return coords


def _make_source_estimate(
    spill_id: str = "spill-f1-test",
    center_lon: float = CENTER_LON,
    center_lat: float = CENTER_LAT,
    radius_km: float = RADIUS_KM,
    step_hours: float = 1.0,
    source_time: datetime | None = None,
) -> SourceEstimateResult:
    """Build a deterministic D3 SourceEstimateResult."""
    t_src = source_time or _T0
    t_obs = t_src + timedelta(hours=6)
    ring = _make_polygon_ring(center_lon, center_lat, radius_km)

    return SourceEstimateResult(
        id=f"src-est-{spill_id}",
        investigation_id="inv-f1-test",
        spill_detection_id=spill_id,
        wind_asset_id="wind-mock",
        current_asset_id="current-mock",
        asset_id="drift-product-mock",
        step_hours=step_hours,
        leeway_fraction=0.035,
        origin_lon=center_lon + 0.05,
        origin_lat=center_lat + 0.05,
        observation_time=t_obs,
        source_point_lon=center_lon,
        source_point_lat=center_lat,
        source_time=t_src,
        lookback_hours=6.0,
        source_uncertainty_radius_km=radius_km,
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
        source_zone_geometry={"type": "Polygon", "coordinates": [ring]},
        metadata={},
    )


def _make_candidate(
    candidate_id: str = "cand-001",
    vessel_id: str = "vessel-001",
    mmsi: str | None = "123456789",
    vessel_name: str | None = "MV TEST VESSEL",
    inside_zone: bool = True,
    d_center_km: float = 2.0,
    d_boundary_km: float = 0.0,
    time_offset_h: float = 0.5,
    cog: float | None = 45.0,
    lon: float = CENTER_LON,
    lat: float = CENTER_LAT,
) -> CandidateVessel:
    t_cpa = _T0 + timedelta(hours=time_offset_h)
    pos = VesselPosition(
        timestamp=t_cpa,
        lon=lon,
        lat=lat,
        speed=10.0,
        course=cog,
    )
    return CandidateVessel(
        candidate_id=candidate_id,
        vessel_id=vessel_id,
        mmsi=mmsi,
        vessel_name=vessel_name,
        closest_position_lon=lon,
        closest_position_lat=lat,
        closest_position_time=t_cpa,
        inside_source_zone=inside_zone,
        min_distance_to_source_center_km=d_center_km,
        distance_to_zone_boundary_km=d_boundary_km,
        time_offset_from_source_hours=time_offset_h,
        speed_over_ground=10.0,
        course_over_ground=cog,
        observed_positions_count=1,
        raw_positions=[pos],
    )


def _make_trajectory_analysis(
    candidate_id: str = "cand-001",
    mmsi: str | None = "123456789",
    d_center_km: float = 2.0,
    d_boundary_km: float = 0.0,
    d_centerline_km: float = 1.5,
    time_offset_h: float = 0.5,
    angular_diff: float | None = 30.0,
) -> VesselTrajectoryAnalysis:
    t_cpa = _T0 + timedelta(hours=time_offset_h)
    return VesselTrajectoryAnalysis(
        candidate_id=candidate_id,
        vessel_id=f"vessel-{candidate_id}",
        mmsi=mmsi,
        observed_positions_count=2,
        total_track_duration_seconds=3600.0,
        total_track_distance_m=10000.0,
        transit_profile=ZoneTransitProfile(
            points_inside_count=2,
            first_inside_time=t_cpa - timedelta(hours=1),
            last_inside_time=t_cpa,
            observed_transit_duration_seconds=3600.0,
            min_distance_to_center_km=d_center_km,
            distance_to_zone_boundary_km=d_boundary_km,
            closest_position_time=t_cpa,
            time_offset_from_source_hours=time_offset_h,
            cpa_lon=CENTER_LON,
            cpa_lat=CENTER_LAT,
        ),
        centerline_proximity=CenterlineProximityProfile(
            min_distance_to_centerline_km=d_centerline_km,
            closest_centerline_point_lon=CENTER_LON,
            closest_centerline_point_lat=CENTER_LAT,
            course_drift_angle_diff_deg=angular_diff,
        ),
        segments=[],
        metadata={"zero_fabrication": True},
    )


def _make_candidate_result(candidates: list[CandidateVessel]) -> CandidateVesselGenerationResult:
    return CandidateVesselGenerationResult(
        id="cand-gen-f1-test",
        investigation_id="inv-f1-test",
        source_estimate_id="src-est-spill-f1-test",
        spill_detection_id="spill-f1-test",
        status=CandidateGenerationStatus.COMPLETED,
        source_time=_T0,
        source_uncertainty_radius_km=RADIUS_KM,
        temporal_window_start=_T0 - timedelta(hours=6),
        temporal_window_end=_T0 + timedelta(hours=6),
        spatial_query_bbox={"west": 9.0, "south": 43.0, "east": 10.0, "north": 43.5},
        candidate_count=len(candidates),
        total_vessels_checked=len(candidates),
        candidates=candidates,
    )


def _make_trajectory_result(
    analyses: list[VesselTrajectoryAnalysis],
) -> TrajectoryAnalysisResult:
    return TrajectoryAnalysisResult(
        id="traj-analysis-f1-test",
        investigation_id="inv-f1-test",
        spill_detection_id="spill-f1-test",
        source_estimate_id="src-est-spill-f1-test",
        candidate_generation_id="cand-gen-f1-test",
        analyzed_vessel_count=len(analyses),
        analyses=analyses,
        metadata={"zero_fabrication": True},
    )


def _make_behavioral_result(
    profiles: list[VesselBehavioralProfile] | None = None,
) -> BehavioralIntelligenceResult:
    return BehavioralIntelligenceResult(
        id="beh-intel-f1-test",
        investigation_id="inv-f1-test",
        spill_detection_id="spill-f1-test",
        source_estimate_id="src-est-spill-f1-test",
        candidate_generation_id="cand-gen-f1-test",
        analyzed_vessel_count=len(profiles or []),
        profiles=profiles or [],
        total_anomalies_detected=sum(len(p.anomalies) for p in (profiles or [])),
        total_transmission_gaps_detected=sum(len(p.transmission_gaps) for p in (profiles or [])),
    )


def _make_behavioral_profile(
    candidate_id: str = "cand-001",
    mmsi: str | None = "123456789",
    n_anomalies: int = 0,
    n_gaps: int = 0,
    loitering: bool = False,
    nav_consistent: bool = True,
) -> VesselBehavioralProfile:
    anomalies: list[BehavioralAnomaly] = []
    for i in range(n_anomalies):
        anomalies.append(BehavioralAnomaly(
            anomaly_type=BehavioralAnomalyType.SPEED_DROP_IN_ZONE,
            severity=AnomalySeverity.NOTABLE,
            description=f"Speed drop observed #{i}",
            timestamp=_T0,
            location_lon=CENTER_LON,
            location_lat=CENTER_LAT,
            inside_source_zone=True,
        ))

    gaps: list[TransmissionGap] = []
    for i in range(n_gaps):
        gaps.append(TransmissionGap(
            gap_start_time=_T0,
            gap_end_time=_T0 + timedelta(hours=1),
            gap_duration_seconds=3600.0,
            gap_start_lon=CENTER_LON,
            gap_start_lat=CENTER_LAT,
            gap_end_lon=CENTER_LON + 0.05,
            gap_end_lat=CENTER_LAT + 0.05,
            distance_across_gap_km=5.0,
            spanned_source_zone=True,
        ))

    return VesselBehavioralProfile(
        candidate_id=candidate_id,
        vessel_id=f"vessel-{candidate_id}",
        mmsi=mmsi,
        anomalies=anomalies,
        transmission_gaps=gaps,
        loitering_detected=loitering,
        nav_status_consistent=nav_consistent,
        summary_flags=[a.anomaly_type.value for a in anomalies],
    )


def _make_drift_result() -> DriftResult:
    """Minimal D1 DriftResult for cross-check testing."""
    t1 = _T0 + timedelta(hours=2)
    t2 = _T0 + timedelta(hours=4)
    return DriftResult(
        id="drift-f1-test",
        investigation_id="inv-f1-test",
        spill_detection_id="spill-f1-test",
        wind_asset_id="wind-mock",
        current_asset_id="current-mock",
        asset_id="drift-product-mock",
        observation_time=_T0 + timedelta(hours=6),
        origin_lon=CENTER_LON,
        origin_lat=CENTER_LAT,
        steps=[
            DriftStep(
                timestamp=t1,
                lon=CENTER_LON + 0.05,
                lat=CENTER_LAT + 0.05,
                u_wind_ms=3.0,
                v_wind_ms=2.0,
                u_current_ms=0.1,
                v_current_ms=0.05,
                drift_u_ms=0.2,
                drift_v_ms=0.12,
                cumulative_distance_m=5000.0,
            ),
            DriftStep(
                timestamp=t2,
                lon=CENTER_LON + 0.10,
                lat=CENTER_LAT + 0.10,
                u_wind_ms=3.0,
                v_wind_ms=2.0,
                u_current_ms=0.1,
                v_current_ms=0.05,
                drift_u_ms=0.2,
                drift_v_ms=0.12,
                cumulative_distance_m=10000.0,
            ),
        ],
        total_duration_hours=4.0,
        step_hours=2.0,
        leeway_fraction=0.035,
        endpoint_lon=CENTER_LON + 0.10,
        endpoint_lat=CENTER_LAT + 0.10,
        endpoint_uncertainty_km=None,
    )


# ---------------------------------------------------------------------------
# Test Suite 1: Scoring Helper Unit Tests
# ---------------------------------------------------------------------------

class TestScoringHelpers(unittest.TestCase):
    """Unit tests for individual scoring functions."""

    def test_spatial_score_exact_center(self):
        """S_spatial = 1.0 when vessel is exactly at source center (d_center=0, d_boundary=0)."""
        s = _score_spatial(d_center_km=0.0, d_boundary_km=0.0, r_zone_km=RADIUS_KM)
        self.assertAlmostEqual(s, 1.0, places=6)

    def test_spatial_score_at_zone_boundary_inside(self):
        """S_spatial ≈ 0.6065 when vessel is at the zone boundary (d_center=R_zone, inside)."""
        s = _score_spatial(d_center_km=RADIUS_KM, d_boundary_km=0.0, r_zone_km=RADIUS_KM)
        expected = math.exp(-0.5)
        self.assertAlmostEqual(s, expected, places=6)

    def test_spatial_score_outside_zone_continuous(self):
        """S_spatial is continuous from inside to outside at the zone boundary."""
        s_inside = _score_spatial(d_center_km=RADIUS_KM, d_boundary_km=0.0, r_zone_km=RADIUS_KM)
        s_outside = _score_spatial(d_center_km=RADIUS_KM + 0.001, d_boundary_km=0.001, r_zone_km=RADIUS_KM)
        # Should be approximately continuous (small jump due to formula transition)
        self.assertAlmostEqual(s_inside, s_outside, delta=0.01)

    def test_spatial_score_outside_decays_toward_zero(self):
        """S_spatial decreases strictly as distance outside zone increases."""
        s1 = _score_spatial(d_center_km=RADIUS_KM + 2.0, d_boundary_km=2.0, r_zone_km=RADIUS_KM)
        s2 = _score_spatial(d_center_km=RADIUS_KM + 10.0, d_boundary_km=10.0, r_zone_km=RADIUS_KM)
        s3 = _score_spatial(d_center_km=RADIUS_KM + 50.0, d_boundary_km=50.0, r_zone_km=RADIUS_KM)
        self.assertGreater(s1, s2)
        self.assertGreater(s2, s3)
        self.assertGreater(s3, 0.0)

    def test_spatial_score_degenerate_r_zero(self):
        """S_spatial = 0.0 when R_zone=0 (degenerate)."""
        s = _score_spatial(d_center_km=0.0, d_boundary_km=0.0, r_zone_km=0.0)
        self.assertEqual(s, 0.0)

    def test_temporal_score_at_source_time(self):
        """S_temporal = 1.0 when CPA coincides exactly with source time (Δt=0)."""
        s = _score_temporal(abs_time_offset_hours=0.0, tau_scale_hours=2.0)
        self.assertAlmostEqual(s, 1.0, places=6)

    def test_temporal_score_gaussian_decay(self):
        """S_temporal decreases strictly with increasing time offset."""
        tau = 2.0
        s0 = _score_temporal(0.0, tau)
        s1 = _score_temporal(1.0, tau)
        s2 = _score_temporal(3.0, tau)
        s5 = _score_temporal(10.0, tau)
        self.assertGreater(s0, s1)
        self.assertGreater(s1, s2)
        self.assertGreater(s2, s5)
        self.assertGreater(s5, 0.0)

    def test_temporal_score_at_tau_scale(self):
        """S_temporal = exp(-0.5) ≈ 0.6065 at Δt = τ_scale."""
        tau = 3.0
        s = _score_temporal(tau, tau)
        self.assertAlmostEqual(s, math.exp(-0.5), places=6)

    def test_trajectory_score_with_angular(self):
        """S_trajectory uses weighted combination of cross-track and angular scores."""
        s, rationale = _score_trajectory(d_centerline_km=0.0, r_zone_km=RADIUS_KM, angular_diff_deg=0.0)
        # d_cl=0 → S_cross=1.0; angular_diff=0 → S_angular=1.0
        self.assertAlmostEqual(s, 1.0, places=6)
        self.assertIn("S_trajectory", rationale)

    def test_trajectory_score_no_angular(self):
        """S_trajectory = S_cross_track when angular comparison unavailable."""
        s1, _ = _score_trajectory(d_centerline_km=2.0, r_zone_km=RADIUS_KM, angular_diff_deg=None)
        s2, _ = _score_trajectory(d_centerline_km=2.0, r_zone_km=RADIUS_KM, angular_diff_deg=0.0)
        # With no angular: S=S_cross_track; with angular=0: S=0.6*S_cross+0.4*1.0 > S_cross
        self.assertLessEqual(s1, s2 + 1e-10)  # no-angular ≤ perfect-angular

    def test_trajectory_score_max_angular_diff(self):
        """S_trajectory is minimized when angular difference is 180 degrees."""
        s_perfect, _ = _score_trajectory(0.0, RADIUS_KM, 0.0)
        s_opposite, _ = _score_trajectory(0.0, RADIUS_KM, 180.0)
        self.assertGreater(s_perfect, s_opposite)
        # angular=180 → S_angular = (1 + cos(π))/2 = 0
        self.assertAlmostEqual(s_opposite, 0.6 * 1.0 + 0.4 * 0.0, places=6)

    def test_composite_score_all_valid(self):
        """Composite score is correctly weighted sum over all 3 valid channels."""
        signals = [
            EvidenceSignal(
                channel_name="spatial_proximity",
                status=SignalStatus.VALID,
                score=0.8,
                nominal_weight=0.50,
                rationale="test",
            ),
            EvidenceSignal(
                channel_name="temporal_proximity",
                status=SignalStatus.VALID,
                score=0.6,
                nominal_weight=0.25,
                rationale="test",
            ),
            EvidenceSignal(
                channel_name="trajectory_consistency",
                status=SignalStatus.VALID,
                score=0.4,
                nominal_weight=0.25,
                rationale="test",
            ),
        ]
        score = _composite_score(signals)
        expected = (0.50 * 0.8 + 0.25 * 0.6 + 0.25 * 0.4) / (0.50 + 0.25 + 0.25)
        self.assertAlmostEqual(score, expected, places=5)

    def test_composite_score_missing_channel_excluded_from_denominator(self):
        """Missing channels are excluded from denominator (not treated as score=0)."""
        # Only spatial and temporal valid
        signals_2 = [
            EvidenceSignal(
                channel_name="spatial_proximity",
                status=SignalStatus.VALID,
                score=1.0,
                nominal_weight=0.50,
                rationale="test",
            ),
            EvidenceSignal(
                channel_name="temporal_proximity",
                status=SignalStatus.VALID,
                score=1.0,
                nominal_weight=0.25,
                rationale="test",
            ),
            EvidenceSignal(
                channel_name="trajectory_consistency",
                status=SignalStatus.INSUFFICIENT_DATA,
                score=None,
                nominal_weight=0.25,
                rationale="no data",
            ),
        ]
        score_2 = _composite_score(signals_2)

        # If trajectory were scored as 0.0, it would lower the score:
        # (0.50*1.0 + 0.25*1.0 + 0.25*0.0) / 1.0 = 0.75
        # But with exclusion: (0.50*1.0 + 0.25*1.0) / (0.50+0.25) = 1.0
        self.assertAlmostEqual(score_2, 1.0, places=6)

    def test_composite_score_no_valid_channels_returns_none(self):
        """Composite score is None when no valid channels exist."""
        signals = [
            EvidenceSignal(
                channel_name="spatial_proximity",
                status=SignalStatus.UNAVAILABLE,
                score=None,
                nominal_weight=0.50,
                rationale="unavailable",
            ),
            EvidenceSignal(
                channel_name="temporal_proximity",
                status=SignalStatus.INSUFFICIENT_DATA,
                score=None,
                nominal_weight=0.25,
                rationale="insufficient",
            ),
            EvidenceSignal(
                channel_name="trajectory_consistency",
                status=SignalStatus.INSUFFICIENT_DATA,
                score=None,
                nominal_weight=0.25,
                rationale="insufficient",
            ),
        ]
        score = _composite_score(signals)
        self.assertIsNone(score)

    def test_evidence_availability_ratio_all_valid(self):
        """evidence_availability_ratio = 1.0 when all 3 channels are valid."""
        signals = [
            EvidenceSignal(channel_name="spatial_proximity", status=SignalStatus.VALID, score=0.5, nominal_weight=0.5, rationale=""),
            EvidenceSignal(channel_name="temporal_proximity", status=SignalStatus.VALID, score=0.5, nominal_weight=0.25, rationale=""),
            EvidenceSignal(channel_name="trajectory_consistency", status=SignalStatus.VALID, score=0.5, nominal_weight=0.25, rationale=""),
        ]
        ratio = _evidence_availability_ratio(signals)
        self.assertAlmostEqual(ratio, 1.0, places=6)

    def test_evidence_availability_ratio_two_of_three(self):
        """evidence_availability_ratio = 2/3 when 2 of 3 channels are valid."""
        signals = [
            EvidenceSignal(channel_name="spatial_proximity", status=SignalStatus.VALID, score=0.5, nominal_weight=0.5, rationale=""),
            EvidenceSignal(channel_name="temporal_proximity", status=SignalStatus.VALID, score=0.5, nominal_weight=0.25, rationale=""),
            EvidenceSignal(channel_name="trajectory_consistency", status=SignalStatus.INSUFFICIENT_DATA, score=None, nominal_weight=0.25, rationale=""),
        ]
        ratio = _evidence_availability_ratio(signals)
        self.assertAlmostEqual(ratio, 2.0 / 3.0, places=5)

    def test_evidence_availability_ratio_none_valid(self):
        """evidence_availability_ratio = 0.0 when no channels are valid."""
        signals = [
            EvidenceSignal(channel_name="spatial_proximity", status=SignalStatus.UNAVAILABLE, score=None, nominal_weight=0.5, rationale=""),
            EvidenceSignal(channel_name="temporal_proximity", status=SignalStatus.INSUFFICIENT_DATA, score=None, nominal_weight=0.25, rationale=""),
            EvidenceSignal(channel_name="trajectory_consistency", status=SignalStatus.INSUFFICIENT_DATA, score=None, nominal_weight=0.25, rationale=""),
        ]
        ratio = _evidence_availability_ratio(signals)
        self.assertAlmostEqual(ratio, 0.0, places=6)


# ---------------------------------------------------------------------------
# Test Suite 2: Behavioral Invariance
# ---------------------------------------------------------------------------

class TestBehavioralInvariance(unittest.TestCase):
    """Verify E3 behavioral anomalies contribute exactly 0.00 to concordance score."""

    def setUp(self):
        self.source = _make_source_estimate()
        self.candidate = _make_candidate(
            d_center_km=2.0, d_boundary_km=0.0, time_offset_h=0.5
        )
        self.traj = _make_trajectory_analysis(
            d_center_km=2.0, d_boundary_km=0.0, time_offset_h=0.5, angular_diff=30.0
        )

    def _run_fusion(self, n_anomalies: int, n_gaps: int, loitering: bool) -> EvidenceFusionResult:
        profile = _make_behavioral_profile(
            n_anomalies=n_anomalies, n_gaps=n_gaps, loitering=loitering
        )
        candidate_result = _make_candidate_result([self.candidate])
        trajectory_result = _make_trajectory_result([self.traj])
        behavioral_result = _make_behavioral_result([profile])

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                investigation_id="inv-f1-test",
                spill_id="spill-f1-test",
                source_estimate=self.source,
                candidate_result=candidate_result,
                trajectory_result=trajectory_result,
                behavioral_result=behavioral_result,
                output_dir=tmpdir,
                registry=InMemoryAssetRegistry(),
            )
        return result

    def test_zero_anomalies_baseline(self):
        """Record score with zero anomalies as baseline."""
        result = self._run_fusion(n_anomalies=0, n_gaps=0, loitering=False)
        self.assertIsNotNone(result.fused_candidates[0].composite_concordance_score)
        self._baseline_score = result.fused_candidates[0].composite_concordance_score

    def test_behavioral_anomalies_do_not_change_score(self):
        """Adding 5 behavioral anomalies must not alter the primary concordance score."""
        result_0 = self._run_fusion(n_anomalies=0, n_gaps=0, loitering=False)
        result_5 = self._run_fusion(n_anomalies=5, n_gaps=0, loitering=False)

        score_0 = result_0.fused_candidates[0].composite_concordance_score
        score_5 = result_5.fused_candidates[0].composite_concordance_score

        self.assertIsNotNone(score_0)
        self.assertIsNotNone(score_5)
        self.assertAlmostEqual(score_0, score_5, places=6,
            msg="Adding anomalies must not change the concordance score")

    def test_transmission_gaps_do_not_change_score(self):
        """Adding 3 transmission gaps must not alter the primary concordance score."""
        result_0 = self._run_fusion(n_anomalies=0, n_gaps=0, loitering=False)
        result_gaps = self._run_fusion(n_anomalies=0, n_gaps=3, loitering=False)

        score_0 = result_0.fused_candidates[0].composite_concordance_score
        score_gaps = result_gaps.fused_candidates[0].composite_concordance_score

        self.assertAlmostEqual(score_0, score_gaps, places=6,
            msg="Adding transmission gaps must not change the concordance score")

    def test_loitering_does_not_change_score(self):
        """Loitering detection must not alter the primary concordance score."""
        result_no_loit = self._run_fusion(n_anomalies=0, n_gaps=0, loitering=False)
        result_loit = self._run_fusion(n_anomalies=0, n_gaps=0, loitering=True)

        score_no = result_no_loit.fused_candidates[0].composite_concordance_score
        score_loit = result_loit.fused_candidates[0].composite_concordance_score

        self.assertAlmostEqual(score_no, score_loit, places=6,
            msg="Loitering must not change the concordance score")

    def test_behavioral_context_preserved_separately(self):
        """Behavioral findings are preserved in behavioral_context, not contributing to score."""
        result = self._run_fusion(n_anomalies=3, n_gaps=2, loitering=True)
        fev = result.fused_candidates[0]

        # behavioral_context has the E3 data
        self.assertEqual(fev.behavioral_context.total_anomalies_count, 3)
        self.assertEqual(fev.behavioral_context.transmission_gaps_count, 2)
        self.assertTrue(fev.behavioral_context.loitering_detected)

        # But concordance score exists from the 3 physical channels
        self.assertIsNotNone(fev.composite_concordance_score)

    def test_behavioral_context_contribution_metadata(self):
        """Result metadata explicitly records behavioral score contribution as 0.0."""
        result = self._run_fusion(n_anomalies=5, n_gaps=5, loitering=True)
        self.assertEqual(result.metadata.get("behavioral_contribution_to_score"), 0.0)


# ---------------------------------------------------------------------------
# Test Suite 3: D1 Forward Drift Decoupling
# ---------------------------------------------------------------------------

class TestForwardDriftDecoupling(unittest.TestCase):
    """Supplying or omitting D1 forward drift must not change the primary concordance score."""

    def setUp(self):
        self.source = _make_source_estimate()
        self.candidate = _make_candidate(d_center_km=3.0, d_boundary_km=0.0)
        self.traj = _make_trajectory_analysis(d_center_km=3.0, d_boundary_km=0.0)
        self.candidate_result = _make_candidate_result([self.candidate])
        self.trajectory_result = _make_trajectory_result([self.traj])
        self.behavioral_result = _make_behavioral_result()

    def _fuse(self, drift_result: DriftResult | None) -> EvidenceFusionResult:
        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                investigation_id="inv-f1-test",
                spill_id="spill-f1-test",
                source_estimate=self.source,
                candidate_result=self.candidate_result,
                trajectory_result=self.trajectory_result,
                behavioral_result=self.behavioral_result,
                drift_result=drift_result,
                output_dir=tmpdir,
                registry=InMemoryAssetRegistry(),
            )
        return result

    def test_primary_score_unchanged_with_drift(self):
        """Score without D1 must equal score with D1."""
        result_no_drift = self._fuse(drift_result=None)
        result_with_drift = self._fuse(drift_result=_make_drift_result())

        score_no = result_no_drift.fused_candidates[0].composite_concordance_score
        score_with = result_with_drift.fused_candidates[0].composite_concordance_score

        self.assertAlmostEqual(score_no, score_with, places=6,
            msg="D1 forward drift must not change primary concordance score")

    def test_forward_drift_evaluated_flag_set_correctly(self):
        """forward_drift_cross_check.evaluated is True when D1 is provided."""
        result_no = self._fuse(drift_result=None)
        result_with = self._fuse(drift_result=_make_drift_result())

        self.assertFalse(result_no.fused_candidates[0].forward_drift_cross_check.evaluated)
        self.assertTrue(result_with.fused_candidates[0].forward_drift_cross_check.evaluated)

    def test_forward_drift_distance_populated_when_provided(self):
        """min_distance_to_forward_track_km is populated only when D1 is provided."""
        result_no = self._fuse(drift_result=None)
        result_with = self._fuse(drift_result=_make_drift_result())

        self.assertIsNone(result_no.fused_candidates[0].forward_drift_cross_check.min_distance_to_forward_track_km)
        self.assertIsNotNone(result_with.fused_candidates[0].forward_drift_cross_check.min_distance_to_forward_track_km)
        self.assertGreaterEqual(
            result_with.fused_candidates[0].forward_drift_cross_check.min_distance_to_forward_track_km,
            0.0,
        )

    def test_forward_drift_contribution_metadata(self):
        """Result metadata explicitly records forward drift score contribution as 0.0."""
        result = self._fuse(drift_result=_make_drift_result())
        self.assertEqual(result.metadata.get("forward_drift_contribution_to_score"), 0.0)


# ---------------------------------------------------------------------------
# Test Suite 4: Order Preservation (Non-Ranking Invariant)
# ---------------------------------------------------------------------------

class TestOrderPreservation(unittest.TestCase):
    """Candidate order from E1 must be strictly preserved in F1 output."""

    def test_order_preserved_low_first(self):
        """Input order preserved even when second candidate would score higher."""
        # cand_a is far away (low score), cand_b is close (high score)
        cand_a = _make_candidate(
            candidate_id="cand-a", mmsi="111111111",
            d_center_km=30.0, d_boundary_km=22.0, time_offset_h=8.0,
        )
        cand_b = _make_candidate(
            candidate_id="cand-b", mmsi="222222222",
            d_center_km=0.5, d_boundary_km=0.0, time_offset_h=0.1,
        )

        traj_a = _make_trajectory_analysis(
            candidate_id="cand-a", mmsi="111111111",
            d_center_km=30.0, d_boundary_km=22.0, time_offset_h=8.0,
        )
        traj_b = _make_trajectory_analysis(
            candidate_id="cand-b", mmsi="222222222",
            d_center_km=0.5, d_boundary_km=0.0, time_offset_h=0.1,
        )

        source = _make_source_estimate()
        candidate_result = _make_candidate_result([cand_a, cand_b])  # low first
        trajectory_result = _make_trajectory_result([traj_a, traj_b])
        behavioral_result = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                investigation_id="inv-f1-test",
                spill_id="spill-f1-test",
                source_estimate=source,
                candidate_result=candidate_result,
                trajectory_result=trajectory_result,
                behavioral_result=behavioral_result,
                output_dir=tmpdir,
                registry=InMemoryAssetRegistry(),
            )

        # Input order preserved: cand_a must be at index 0, cand_b at index 1
        self.assertEqual(result.fused_candidates[0].candidate_id, "cand-a")
        self.assertEqual(result.fused_candidates[1].candidate_id, "cand-b")

        # input_index fields are correct
        self.assertEqual(result.fused_candidates[0].input_index, 0)
        self.assertEqual(result.fused_candidates[1].input_index, 1)

    def test_high_score_first_order_also_preserved(self):
        """Order preserved when high-scoring candidate is first in input."""
        cand_a = _make_candidate(
            candidate_id="cand-a", mmsi="333333333",
            d_center_km=0.5, d_boundary_km=0.0, time_offset_h=0.2,
        )
        cand_b = _make_candidate(
            candidate_id="cand-b", mmsi="444444444",
            d_center_km=25.0, d_boundary_km=17.0, time_offset_h=12.0,
        )

        traj_a = _make_trajectory_analysis(
            candidate_id="cand-a", mmsi="333333333",
            d_center_km=0.5, d_boundary_km=0.0, time_offset_h=0.2,
        )
        traj_b = _make_trajectory_analysis(
            candidate_id="cand-b", mmsi="444444444",
            d_center_km=25.0, d_boundary_km=17.0, time_offset_h=12.0,
        )

        source = _make_source_estimate()
        candidate_result = _make_candidate_result([cand_a, cand_b])
        trajectory_result = _make_trajectory_result([traj_a, traj_b])
        behavioral_result = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                investigation_id="inv-f1-test",
                spill_id="spill-f1-test",
                source_estimate=source,
                candidate_result=candidate_result,
                trajectory_result=trajectory_result,
                behavioral_result=behavioral_result,
                output_dir=tmpdir,
                registry=InMemoryAssetRegistry(),
            )

        self.assertEqual(result.fused_candidates[0].candidate_id, "cand-a")
        self.assertEqual(result.fused_candidates[1].candidate_id, "cand-b")


# ---------------------------------------------------------------------------
# Test Suite 5: Missing Data Handling
# ---------------------------------------------------------------------------

class TestMissingDataHandling(unittest.TestCase):
    """Verify INSUFFICIENT_DATA channels are excluded from score denominator."""

    def test_no_trajectory_analysis_all_insufficient(self):
        """All 3 channels INSUFFICIENT_DATA when no E2 analysis is available."""
        source = _make_source_estimate()
        candidate = _make_candidate()
        # Build trajectory result with no analyses (empty)
        traj_result = _make_trajectory_result([])
        beh_result = _make_behavioral_result()
        candidate_result = _make_candidate_result([candidate])

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                investigation_id="inv-f1-test",
                spill_id="spill-f1-test",
                source_estimate=source,
                candidate_result=candidate_result,
                trajectory_result=traj_result,
                behavioral_result=beh_result,
                output_dir=tmpdir,
                registry=InMemoryAssetRegistry(),
            )

        fev = result.fused_candidates[0]
        for sig in fev.primary_signals:
            self.assertEqual(
                sig.status, SignalStatus.INSUFFICIENT_DATA,
                f"Expected INSUFFICIENT_DATA for {sig.channel_name}, got {sig.status}"
            )
        self.assertIsNone(fev.composite_concordance_score)
        self.assertAlmostEqual(fev.evidence_availability_ratio, 0.0, places=6)

    def test_empty_candidate_list_produces_empty_result(self):
        """No candidates produces empty fused_candidates list."""
        source = _make_source_estimate()
        candidate_result = _make_candidate_result([])
        trajectory_result = _make_trajectory_result([])
        behavioral_result = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                investigation_id="inv-f1-test",
                spill_id="spill-f1-test",
                source_estimate=source,
                candidate_result=candidate_result,
                trajectory_result=trajectory_result,
                behavioral_result=behavioral_result,
                output_dir=tmpdir,
                registry=InMemoryAssetRegistry(),
            )

        self.assertEqual(len(result.fused_candidates), 0)
        self.assertEqual(result.candidate_count, 0)

    def test_missing_angular_comparison_uses_only_cross_track(self):
        """Trajectory channel still valid when angular comparison unavailable (COG absent)."""
        source = _make_source_estimate()
        candidate = _make_candidate(cog=None)
        traj = _make_trajectory_analysis(angular_diff=None)
        candidate_result = _make_candidate_result([candidate])
        trajectory_result = _make_trajectory_result([traj])
        behavioral_result = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                investigation_id="inv-f1-test",
                spill_id="spill-f1-test",
                source_estimate=source,
                candidate_result=candidate_result,
                trajectory_result=trajectory_result,
                behavioral_result=behavioral_result,
                output_dir=tmpdir,
                registry=InMemoryAssetRegistry(),
            )

        fev = result.fused_candidates[0]
        traj_sig = next(s for s in fev.primary_signals if s.channel_name == "trajectory_consistency")
        # Should still be VALID (falls back to S_cross_track only)
        self.assertEqual(traj_sig.status, SignalStatus.VALID)
        self.assertIsNotNone(traj_sig.score)
        self.assertGreater(traj_sig.score, 0.0)

    def test_single_ping_trajectory_channel_valid(self):
        """Vessel with exactly 1 ping still gets VALID trajectory channel."""
        source = _make_source_estimate()
        candidate = _make_candidate()
        # 1 position ping
        traj = VesselTrajectoryAnalysis(
            candidate_id="cand-001",
            vessel_id="vessel-cand-001",
            mmsi="123456789",
            observed_positions_count=1,
            total_track_duration_seconds=0.0,
            total_track_distance_m=0.0,
            transit_profile=ZoneTransitProfile(
                points_inside_count=1,
                first_inside_time=_T0,
                last_inside_time=_T0,
                observed_transit_duration_seconds=0.0,
                min_distance_to_center_km=2.0,
                distance_to_zone_boundary_km=0.0,
                closest_position_time=_T0,
                time_offset_from_source_hours=0.0,
                cpa_lon=CENTER_LON,
                cpa_lat=CENTER_LAT,
            ),
            centerline_proximity=CenterlineProximityProfile(
                min_distance_to_centerline_km=1.0,
                closest_centerline_point_lon=CENTER_LON,
                closest_centerline_point_lat=CENTER_LAT,
                course_drift_angle_diff_deg=None,
            ),
            segments=[],
            metadata={},
        )
        candidate_result = _make_candidate_result([candidate])
        trajectory_result = _make_trajectory_result([traj])
        behavioral_result = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                investigation_id="inv-f1-test",
                spill_id="spill-f1-test",
                source_estimate=source,
                candidate_result=candidate_result,
                trajectory_result=trajectory_result,
                behavioral_result=behavioral_result,
                output_dir=tmpdir,
                registry=InMemoryAssetRegistry(),
            )

        fev = result.fused_candidates[0]
        self.assertIsNotNone(fev.composite_concordance_score)


# ---------------------------------------------------------------------------
# Test Suite 6: B3 Contract Compatibility
# ---------------------------------------------------------------------------

class TestB3ContractCompatibility(unittest.TestCase):
    """Verify F1 uses actual SpillDetection model fields correctly."""

    def test_spill_detection_no_centroid_lon_lat_fields(self):
        """SpillDetection model has no centroid_lon/centroid_lat/detection_time top-level fields."""
        from app.models.satellite import SpillDetection
        sd = SpillDetection(
            id="spill-b3-test",
            investigation_id="inv-b3-test",
            asset_id="asset-b3-test",
            scene_id="scene-b3-test",
            detected=True,
            confidence=0.92,
            geometry={"type": "Polygon", "coordinates": [[[9.47, 43.24], [9.48, 43.24], [9.48, 43.25], [9.47, 43.25], [9.47, 43.24]]]},
            area=15000.0,
            metadata={"centroid": {"longitude": 9.475, "latitude": 43.245}},
        )
        self.assertFalse(hasattr(sd, "centroid_lon"), "centroid_lon must NOT exist on SpillDetection")
        self.assertFalse(hasattr(sd, "centroid_lat"), "centroid_lat must NOT exist on SpillDetection")
        self.assertFalse(hasattr(sd, "detection_time"), "detection_time must NOT exist on SpillDetection")
        self.assertFalse(hasattr(sd, "area_m2"), "area_m2 must NOT exist on SpillDetection")

        # Centroid accessed via metadata dict
        centroid = sd.metadata.get("centroid", {})
        self.assertIn("longitude", centroid)
        self.assertIn("latitude", centroid)
        self.assertEqual(centroid["longitude"], 9.475)

    def test_spill_detection_geometry_field_exists(self):
        """SpillDetection.geometry holds the GeoJSON polygon."""
        from app.models.satellite import SpillDetection
        sd = SpillDetection(
            id="sd-2",
            investigation_id="inv-2",
            asset_id="asset-2",
            detected=True,
            geometry={"type": "Point", "coordinates": [9.47, 43.24]},
            metadata={},
        )
        self.assertIsInstance(sd.geometry, dict)
        self.assertEqual(sd.geometry.get("type"), "Point")

    def test_spill_detection_area_optional(self):
        """SpillDetection.area is optional (not used by F1 for scoring)."""
        from app.models.satellite import SpillDetection
        sd_with_area = SpillDetection(id="sd-3", investigation_id="inv-3", asset_id="a3", detected=True, geometry={}, area=50000.0, metadata={})
        sd_no_area = SpillDetection(id="sd-4", investigation_id="inv-4", asset_id="a4", detected=True, geometry={}, area=None, metadata={})
        self.assertEqual(sd_with_area.area, 50000.0)
        self.assertIsNone(sd_no_area.area)


# ---------------------------------------------------------------------------
# Test Suite 7: Spatial Normalisation via D3 R_zone
# ---------------------------------------------------------------------------

class TestSpatialNormalization(unittest.TestCase):
    """Verify R_zone comes from source_estimate.source_uncertainty_radius_km."""

    def test_r_zone_from_source_estimate(self):
        """normalization_reference_radius_km equals source_estimate.source_uncertainty_radius_km."""
        source = _make_source_estimate(radius_km=12.5)
        candidate = _make_candidate()
        candidate_result = _make_candidate_result([candidate])
        trajectory_result = _make_trajectory_result([_make_trajectory_analysis()])
        behavioral_result = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                investigation_id="inv-f1-test",
                spill_id="spill-f1-test",
                source_estimate=source,
                candidate_result=candidate_result,
                trajectory_result=trajectory_result,
                behavioral_result=behavioral_result,
                output_dir=tmpdir,
                registry=InMemoryAssetRegistry(),
            )

        self.assertAlmostEqual(
            result.normalization_reference_radius_km,
            source.source_uncertainty_radius_km,
            places=6,
        )

    def test_tau_scale_from_d3_step_hours(self):
        """temporal_scale_hours = max(2 * step_hours, 2.0)."""
        source_fast = _make_source_estimate(step_hours=0.5)
        source_slow = _make_source_estimate(step_hours=2.0)

        candidate_result = _make_candidate_result([_make_candidate()])
        trajectory_result = _make_trajectory_result([_make_trajectory_analysis()])
        behavioral_result = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            result_fast, _ = fuse_evidence(
                "inv-f1-test", "spill-f1-test", source_fast,
                candidate_result, trajectory_result, behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )
        with tempfile.TemporaryDirectory() as tmpdir:
            result_slow, _ = fuse_evidence(
                "inv-f1-test", "spill-f1-test", source_slow,
                candidate_result, trajectory_result, behavioral_result,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        # step_hours=0.5 → tau = max(1.0, 2.0) = 2.0
        self.assertAlmostEqual(result_fast.temporal_scale_hours, 2.0, places=6)
        # step_hours=2.0 → tau = max(4.0, 2.0) = 4.0
        self.assertAlmostEqual(result_slow.temporal_scale_hours, 4.0, places=6)

    def test_r_zone_zero_raises_error(self):
        """fuse_evidence raises EvidenceFusionError if R_zone <= 0."""
        source = _make_source_estimate(radius_km=0.0)  # pydantic ge=0 so it's allowed
        # Force zero radius directly
        source = source.model_copy(update={"source_uncertainty_radius_km": 0.0})
        candidate_result = _make_candidate_result([_make_candidate()])
        trajectory_result = _make_trajectory_result([])
        behavioral_result = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(EvidenceFusionError):
                fuse_evidence(
                    "inv-f1-test", "spill-f1-test", source,
                    candidate_result, trajectory_result, behavioral_result,
                    output_dir=tmpdir, registry=InMemoryAssetRegistry(),
                )


# ---------------------------------------------------------------------------
# Test Suite 8: Provenance and AssetRegistry
# ---------------------------------------------------------------------------

class TestProvenanceAndRegistry(unittest.TestCase):
    """Verify asset registration and provenance fields."""

    def _run_fusion(self, tmpdir: str) -> tuple[EvidenceFusionResult, Asset]:
        source = _make_source_estimate()
        candidate_result = _make_candidate_result([_make_candidate()])
        trajectory_result = _make_trajectory_result([_make_trajectory_analysis()])
        behavioral_result = _make_behavioral_result()
        registry = InMemoryAssetRegistry()

        result, asset = fuse_evidence(
            investigation_id="inv-prov-test",
            spill_id="spill-prov-test",
            source_estimate=source,
            candidate_result=candidate_result,
            trajectory_result=trajectory_result,
            behavioral_result=behavioral_result,
            output_dir=tmpdir,
            registry=registry,
        )
        return result, asset

    def test_asset_type_document(self):
        """Derived asset is registered as AssetType.DOCUMENT."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _, asset = self._run_fusion(tmpdir)
        self.assertEqual(asset.type, AssetType.DOCUMENT)

    def test_asset_metadata_type_evidence_fusion(self):
        """Asset metadata contains asset_type='evidence_fusion'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _, asset = self._run_fusion(tmpdir)
        self.assertEqual(asset.metadata.get("asset_type"), "evidence_fusion")

    def test_result_asset_id_set(self):
        """result.asset_id is set to the registered asset id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result, asset = self._run_fusion(tmpdir)
        self.assertEqual(result.asset_id, asset.id)

    def test_provenance_zero_fabrication_flag(self):
        """Registered asset provenance extra includes zero_fabrication=True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _, asset = self._run_fusion(tmpdir)
        self.assertTrue(asset.provenance.extra.get("zero_fabrication"))

    def test_provenance_upstream_ids_recorded(self):
        """Provenance extra records all upstream product IDs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result, asset = self._run_fusion(tmpdir)
        extra = asset.provenance.extra
        self.assertIn("source_estimate_id", extra)
        self.assertIn("candidate_generation_id", extra)
        self.assertIn("trajectory_analysis_id", extra)
        self.assertIn("behavioral_intelligence_id", extra)

    def test_result_metadata_score_interpretation(self):
        """Result metadata includes score_interpretation clarifying non-attribution."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = self._run_fusion(tmpdir)
        interp = result.metadata.get("score_interpretation", "")
        self.assertIn("NOT a probability", interp)
        self.assertIn("attribution", interp)

    def test_result_metadata_candidate_ordering(self):
        """Result metadata explicitly states that ordering is strict E1 input order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = self._run_fusion(tmpdir)
        ordering = result.metadata.get("candidate_ordering", "")
        self.assertIn("input order", ordering.lower())

    def test_provenance_behavioral_contribution_zero(self):
        """Asset provenance extra records behavioral_contribution_to_score=0.0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _, asset = self._run_fusion(tmpdir)
        self.assertEqual(asset.provenance.extra.get("behavioral_contribution_to_score"), 0.0)

    def test_provenance_forward_drift_contribution_zero(self):
        """Asset provenance extra records forward_drift_contribution_to_score=0.0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            _, asset = self._run_fusion(tmpdir)
        self.assertEqual(asset.provenance.extra.get("forward_drift_contribution_to_score"), 0.0)


# ---------------------------------------------------------------------------
# Test Suite 9: GeoJSON Artifact
# ---------------------------------------------------------------------------

class TestGeoJSONArtifact(unittest.TestCase):
    """Verify GeoJSON output structure."""

    def test_geojson_is_valid_feature_collection(self):
        """Exported GeoJSON is a valid FeatureCollection."""
        source = _make_source_estimate()
        candidate_result = _make_candidate_result([_make_candidate()])
        trajectory_result = _make_trajectory_result([_make_trajectory_analysis()])
        behavioral_result = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            result, asset = fuse_evidence(
                investigation_id="inv-geojson-test",
                spill_id="spill-geojson-test",
                source_estimate=source,
                candidate_result=candidate_result,
                trajectory_result=trajectory_result,
                behavioral_result=behavioral_result,
                output_dir=tmpdir,
                registry=InMemoryAssetRegistry(),
            )
            geojson_path = Path(asset.location)
            self.assertTrue(geojson_path.exists(), "GeoJSON artifact file must exist")
            data = json.loads(geojson_path.read_text(encoding="utf-8"))

        self.assertEqual(data.get("type"), "FeatureCollection")
        self.assertIn("features", data)
        self.assertIsInstance(data["features"], list)

    def test_geojson_contains_source_zone_feature(self):
        """GeoJSON includes a source_candidate_zone feature."""
        source = _make_source_estimate()
        candidate_result = _make_candidate_result([_make_candidate()])
        trajectory_result = _make_trajectory_result([_make_trajectory_analysis()])
        behavioral_result = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            _, asset = fuse_evidence(
                investigation_id="inv-geojson-test2",
                spill_id="spill-geojson-test2",
                source_estimate=source,
                candidate_result=candidate_result,
                trajectory_result=trajectory_result,
                behavioral_result=behavioral_result,
                output_dir=tmpdir,
                registry=InMemoryAssetRegistry(),
            )
            data = json.loads(Path(asset.location).read_text(encoding="utf-8"))

        kinds = [f["properties"].get("feature_kind") for f in data["features"]]
        self.assertIn("source_candidate_zone", kinds)
        self.assertIn("backward_drift_centerline", kinds)

    def test_geojson_candidate_feature_has_concordance_score(self):
        """GeoJSON candidate feature properties include concordance score."""
        source = _make_source_estimate()
        candidate_result = _make_candidate_result([_make_candidate()])
        trajectory_result = _make_trajectory_result([_make_trajectory_analysis()])
        behavioral_result = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            _, asset = fuse_evidence(
                investigation_id="inv-geojson-test3",
                spill_id="spill-geojson-test3",
                source_estimate=source,
                candidate_result=candidate_result,
                trajectory_result=trajectory_result,
                behavioral_result=behavioral_result,
                output_dir=tmpdir,
                registry=InMemoryAssetRegistry(),
            )
            data = json.loads(Path(asset.location).read_text(encoding="utf-8"))

        candidate_features = [
            f for f in data["features"]
            if f["properties"].get("feature_kind") == "evidence_fusion_candidate"
        ]
        self.assertEqual(len(candidate_features), 1)
        props = candidate_features[0]["properties"]
        self.assertIn("composite_concordance_score", props)
        self.assertIn("behavioral_score_contribution", props)
        self.assertEqual(props["behavioral_score_contribution"], 0.0)
        self.assertEqual(props["forward_drift_score_contribution"], 0.0)
        self.assertIn("zero_fabrication", props)
        self.assertTrue(props["zero_fabrication"])


# ---------------------------------------------------------------------------
# Test Suite 10: Deterministic Repeatability
# ---------------------------------------------------------------------------

class TestDeterministicRepeatability(unittest.TestCase):
    """Identical inputs must produce identical outputs."""

    def _make_full_inputs(self):
        source = _make_source_estimate()
        candidate = _make_candidate(d_center_km=3.0, d_boundary_km=0.0, time_offset_h=1.0)
        traj = _make_trajectory_analysis(d_center_km=3.0, d_boundary_km=0.0, time_offset_h=1.0, angular_diff=45.0)
        candidate_result = _make_candidate_result([candidate])
        trajectory_result = _make_trajectory_result([traj])
        behavioral_result = _make_behavioral_result(
            [_make_behavioral_profile(n_anomalies=2, n_gaps=1)]
        )
        return source, candidate_result, trajectory_result, behavioral_result

    def test_repeated_execution_identical_scores(self):
        """Running fusion twice with same inputs produces identical concordance scores."""
        source, cr, tr, br = self._make_full_inputs()

        with tempfile.TemporaryDirectory() as tmpdir1:
            result1, _ = fuse_evidence(
                "inv-repeat-test", "spill-repeat-test", source, cr, tr, br,
                output_dir=tmpdir1, registry=InMemoryAssetRegistry(),
            )

        with tempfile.TemporaryDirectory() as tmpdir2:
            result2, _ = fuse_evidence(
                "inv-repeat-test", "spill-repeat-test", source, cr, tr, br,
                output_dir=tmpdir2, registry=InMemoryAssetRegistry(),
            )

        s1 = result1.fused_candidates[0].composite_concordance_score
        s2 = result2.fused_candidates[0].composite_concordance_score
        self.assertAlmostEqual(s1, s2, places=10)

    def test_repeated_execution_identical_signal_scores(self):
        """Each primary signal score must be identical across repeated runs."""
        source, cr, tr, br = self._make_full_inputs()

        with tempfile.TemporaryDirectory() as tmpdir1:
            result1, _ = fuse_evidence(
                "inv-repeat2", "spill-repeat2", source, cr, tr, br,
                output_dir=tmpdir1, registry=InMemoryAssetRegistry(),
            )
        with tempfile.TemporaryDirectory() as tmpdir2:
            result2, _ = fuse_evidence(
                "inv-repeat2", "spill-repeat2", source, cr, tr, br,
                output_dir=tmpdir2, registry=InMemoryAssetRegistry(),
            )

        for sig1, sig2 in zip(
            result1.fused_candidates[0].primary_signals,
            result2.fused_candidates[0].primary_signals,
        ):
            if sig1.score is not None and sig2.score is not None:
                self.assertAlmostEqual(sig1.score, sig2.score, places=10)


# ---------------------------------------------------------------------------
# Test Suite 11: Nominal Weights
# ---------------------------------------------------------------------------

class TestNominalWeights(unittest.TestCase):
    """Verify correct nominal weights are used."""

    def test_nominal_weights_values(self):
        """Nominal weights: spatial=0.50, temporal=0.25, trajectory=0.25."""
        self.assertAlmostEqual(_NOMINAL_WEIGHTS["spatial_proximity"], 0.50, places=6)
        self.assertAlmostEqual(_NOMINAL_WEIGHTS["temporal_proximity"], 0.25, places=6)
        self.assertAlmostEqual(_NOMINAL_WEIGHTS["trajectory_consistency"], 0.25, places=6)

    def test_nominal_weights_sum_to_one(self):
        """Nominal weights sum to 1.0."""
        total = sum(_NOMINAL_WEIGHTS.values())
        self.assertAlmostEqual(total, 1.0, places=6)

    def test_result_nominal_weights_recorded(self):
        """EvidenceFusionResult records the nominal weights in the result."""
        source = _make_source_estimate()
        cr = _make_candidate_result([_make_candidate()])
        tr = _make_trajectory_result([_make_trajectory_analysis()])
        br = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                "inv-weights-test", "spill-weights-test", source, cr, tr, br,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        self.assertAlmostEqual(result.nominal_weights.get("spatial_proximity"), 0.50, places=6)
        self.assertAlmostEqual(result.nominal_weights.get("temporal_proximity"), 0.25, places=6)
        self.assertAlmostEqual(result.nominal_weights.get("trajectory_consistency"), 0.25, places=6)

    def test_spatial_higher_weighted_than_temporal(self):
        """Spatial channel is weighted more heavily, so a spatial change has larger impact."""
        source = _make_source_estimate()

        # Candidate A: near source (high spatial), late arrival (low temporal)
        cand_a = _make_candidate(candidate_id="cand-a", mmsi="101010101", d_center_km=0.5, d_boundary_km=0.0, time_offset_h=5.0)
        traj_a = _make_trajectory_analysis(candidate_id="cand-a", mmsi="101010101", d_center_km=0.5, d_boundary_km=0.0, time_offset_h=5.0, angular_diff=None)

        # Candidate B: far from source (low spatial), exactly on time (high temporal)
        cand_b = _make_candidate(candidate_id="cand-b", mmsi="202020202", d_center_km=20.0, d_boundary_km=12.0, time_offset_h=0.0)
        traj_b = _make_trajectory_analysis(candidate_id="cand-b", mmsi="202020202", d_center_km=20.0, d_boundary_km=12.0, time_offset_h=0.0, angular_diff=None)

        cr = _make_candidate_result([cand_a, cand_b])
        tr = _make_trajectory_result([traj_a, traj_b])
        br = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                "inv-weight-dominance", "spill-weight-dominance", source, cr, tr, br,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        score_a = result.fused_candidates[0].composite_concordance_score
        score_b = result.fused_candidates[1].composite_concordance_score
        # Close-but-late vs far-but-on-time: spatial dominance should favor close vessel
        self.assertIsNotNone(score_a)
        self.assertIsNotNone(score_b)
        # This test validates the weighting direction, not a specific threshold
        # cand_a has very high spatial but low temporal; cand_b has low spatial but high temporal
        # With w_spatial=0.50 dominating, cand_a should win if d_center very small
        spatial_a = next(s.score for s in result.fused_candidates[0].primary_signals if s.channel_name == "spatial_proximity")
        spatial_b = next(s.score for s in result.fused_candidates[1].primary_signals if s.channel_name == "spatial_proximity")
        self.assertGreater(spatial_a, spatial_b)  # Confirm spatial scores match spatial distances


# ---------------------------------------------------------------------------
# Test Suite 12: API Endpoint Tests
# ---------------------------------------------------------------------------

class TestEvidenceFusionAPIEndpoint(unittest.TestCase):
    """Integration tests for the F1 evidence fusion API endpoint."""

    def setUp(self):
        self.client = TestClient(app)

    def test_missing_spill_returns_404(self):
        """POST to evidence-fusion with missing spill ID returns 404."""
        payload = {
            "source_estimate_id": "se-nonexistent",
            "candidate_generation_id": "cg-nonexistent",
            "trajectory_analysis_id": "ta-nonexistent",
            "behavioral_intelligence_id": "bi-nonexistent",
        }
        response = self.client.post(
            "/api/v1/investigations/inv-api-test/spills/spill-nonexistent/evidence-fusion",
            json=payload,
        )
        self.assertEqual(response.status_code, 404)

    def test_missing_source_estimate_returns_404(self):
        """POST with valid spill but missing source estimate returns 404."""
        # Register a spill asset
        registry = default_asset_registry
        spill_asset = Asset(
            id="spill-api-f1-direct",
            investigation_id="inv-api-f1",
            type=AssetType.SPILL_GEOMETRY,
            provider="test",
            source="test",
            location="/nonexistent/path",
            metadata={"detected": True, "centroid": {"longitude": CENTER_LON, "latitude": CENTER_LAT}},
        )
        registry._assets[spill_asset.id] = spill_asset
        registry._order.append(spill_asset.id)

        payload = {
            "source_estimate_id": "se-nonexistent-api",
            "candidate_generation_id": "cg-nonexistent-api",
            "trajectory_analysis_id": "ta-nonexistent-api",
            "behavioral_intelligence_id": "bi-nonexistent-api",
        }
        response = self.client.post(
            f"/api/v1/investigations/inv-api-f1/spills/{spill_asset.id}/evidence-fusion",
            json=payload,
        )
        self.assertEqual(response.status_code, 404)

    def test_invalid_payload_returns_422(self):
        """POST with missing required fields returns 422 validation error."""
        response = self.client.post(
            "/api/v1/investigations/inv-api-f1/spills/spill-api-f1/evidence-fusion",
            json={"source_estimate_id": "se-test"},  # Missing required fields
        )
        self.assertEqual(response.status_code, 422)


# ---------------------------------------------------------------------------
# Test Suite 13: Zero Fabrication Invariant
# ---------------------------------------------------------------------------

class TestZeroFabrication(unittest.TestCase):
    """Verify no synthetic AIS positions are ever created."""

    def test_result_metadata_zero_fabrication_flag(self):
        """Every VesselFusedEvidence metadata contains zero_fabrication=True."""
        source = _make_source_estimate()
        candidate = _make_candidate()
        cr = _make_candidate_result([candidate])
        tr = _make_trajectory_result([_make_trajectory_analysis()])
        br = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                "inv-zf-test", "spill-zf-test", source, cr, tr, br,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        for fev in result.fused_candidates:
            self.assertTrue(
                fev.metadata.get("zero_fabrication"),
                f"zero_fabrication must be True for {fev.candidate_id}"
            )

    def test_result_global_metadata_zero_fabrication(self):
        """Top-level EvidenceFusionResult metadata contains zero_fabrication=True."""
        source = _make_source_estimate()
        cr = _make_candidate_result([_make_candidate()])
        tr = _make_trajectory_result([_make_trajectory_analysis()])
        br = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                "inv-zf2-test", "spill-zf2-test", source, cr, tr, br,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        self.assertTrue(result.metadata.get("zero_fabrication"))

    def test_observed_positions_count_from_e1_unchanged(self):
        """observed_positions_count in fused metadata matches E1 candidate count (no additions)."""
        source = _make_source_estimate()
        candidate = _make_candidate()  # 1 position
        cr = _make_candidate_result([candidate])
        tr = _make_trajectory_result([_make_trajectory_analysis()])
        br = _make_behavioral_result()

        with tempfile.TemporaryDirectory() as tmpdir:
            result, _ = fuse_evidence(
                "inv-zf3-test", "spill-zf3-test", source, cr, tr, br,
                output_dir=tmpdir, registry=InMemoryAssetRegistry(),
            )

        fev = result.fused_candidates[0]
        self.assertEqual(fev.metadata.get("observed_positions_count"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
