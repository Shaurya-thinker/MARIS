"""Comprehensive scientific test suite for MARIS Stage F2 — Candidate Scoring & Ranking.

Validates the 24 mandatory Stage F2 scientific and architectural requirements:
 1. All evidence available (spatial, temporal, trajectory).
 2. One evidence dimension missing (trajectory missing -> denominator dynamically renormalized).
 3. Multiple evidence dimensions missing (only spatial available -> 100% weight to spatial).
 4. All scoring evidence missing (all channels unavailable -> score is None, never 0.0).
 5. Single AIS observation (sparse_track flag, trajectory unavailable/unverified).
 6. Sparse trajectory (<= 2 observations, quality warnings propagated).
 7. Equal scores (exact tie broken deterministically by secondary criteria).
 8. Near-equal scores (numerical tolerance / deterministic ordering).
 9. Candidate with same score but different availability ratio (higher availability ranks higher).
10. Candidate with same score and availability ratio (spatial discrepancy breaks tie).
11. Invalid negative weight (raises CandidateRankingError / HTTP 422).
12. All weights zero (raises CandidateRankingError / HTTP 422).
13. Deterministic ranking (repeated runs yield identical bit-for-bit ordering).
14. Provenance preservation (is_real_observation=False, data_source_type survived).
15. Uncertainty preservation (propagated source radius, fallback flags).
16. Curated AIS benchmark (MV ULYSSE MMSI 228308800 and CSL VIRGINIA MMSI 229986000).
17. F1 -> F2 serialization round trip (JSON and GeoJSON asset persistence).
18. F2 -> F3 compatibility (ExplainabilityReport seamlessly consumes F2 output).
19. No probability language/value (zero guilt, culprit, or legal responsibility vocabulary).
20. D1/E3 zero-weight behavior (forward drift and behavioral anomalies contribute exactly 0.00).
21. Empty candidate list (handles 0 candidates cleanly without error).
22. One candidate (single candidate ranks #1).
23. Large candidate set (50+ candidates ranked deterministically in O(N x D)).
24. Backward compatibility (preserves existing CandidateRanking contracts).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.acquisition.registry import InMemoryAssetRegistry
from app.acquisition.schemas import AcquiredArtifact
from app.main import app
from app.models.asset import Asset
from app.models.candidate_ranking import (
    CandidateRanking,
    CandidateRankingRequest,
    CandidateRankingResult,
    RankedCandidate,
)
from app.models.common import AssetType, Provenance
from app.models.evidence_fusion import (
    AisEvidenceSummary,
    BehavioralContextSummary,
    CandidateEvidenceDetail,
    DataQualityEvidenceSummary,
    EnvironmentalEvidenceSummary,
    EvidenceAvailabilityProfile,
    EvidenceFusionResult,
    EvidenceProvenanceRecord,
    EvidenceSignal,
    ForwardDriftCrossCheck,
    KinematicEvidenceSummary,
    MultiSourceUncertainty,
    NormalizedEvidenceProfile,
    SignalStatus,
    SourceEvidenceSummary,
    SpatialEvidenceSummary,
    SpillEvidenceSummary,
    TemporalEvidenceSummary,
    VesselFusedEvidence,
)
from app.models.explainability import ExplainabilityReport
from app.services.candidate_ranking import (
    NOMINAL_WEIGHTS,
    CandidateRankingError,
    _ranking_sort_key,
    _validate_weights,
    calculate_candidate_score,
    calculate_candidate_score_profile,
    extract_discrepancies,
    rank_candidates,
)
from app.services.explainability import generate_explainability_report


def _make_f1_candidate(
    candidate_id: str = "cand-001",
    vessel_id: str = "vessel-001",
    mmsi: str | None = "111222333",
    name: str | None = "Test Ship",
    vessel_type: str | None = "Tanker",
    input_index: int = 0,
    spatial_score: float | None = 0.80,
    temporal_score: float | None = 0.60,
    trajectory_score: float | None = 0.40,
    d_center_km: float | None = 2.0,
    delta_t_hours: float | None = 1.0,
    obs_count: int = 4,
    is_real: bool = False,
    data_source_type: str = "curated_historical_reconstruction",
    n_anomalies: int = 0,
    fwd_evaluated: bool = False,
    fwd_dist_km: float | None = None,
    r_zone_km: float = 8.0,
    current_fallback: bool = False,
    shoreline_terminated: bool = False,
) -> VesselFusedEvidence:
    signals = [
        EvidenceSignal(
            channel_name="spatial_proximity",
            status=SignalStatus.VALID if spatial_score is not None else SignalStatus.INSUFFICIENT_DATA,
            score=spatial_score,
            nominal_weight=0.50,
            raw_metrics={"d_center_km": d_center_km},
            rationale="Spatial proximity to source centroid.",
        ),
        EvidenceSignal(
            channel_name="temporal_proximity",
            status=SignalStatus.VALID if temporal_score is not None else SignalStatus.INSUFFICIENT_DATA,
            score=temporal_score,
            nominal_weight=0.25,
            raw_metrics={"delta_t_hours": delta_t_hours},
            rationale="Temporal offset from estimated release.",
        ),
        EvidenceSignal(
            channel_name="trajectory_consistency",
            status=SignalStatus.VALID if trajectory_score is not None else SignalStatus.INSUFFICIENT_DATA,
            score=trajectory_score,
            nominal_weight=0.25,
            raw_metrics={"observed_positions": obs_count},
            rationale="Trajectory alignment with drift axis.",
        ),
    ]

    avail_dims = []
    unavail_dims = []
    if spatial_score is not None:
        avail_dims.append("spatial_proximity")
    else:
        unavail_dims.append("spatial_proximity")
    if temporal_score is not None:
        avail_dims.append("temporal_proximity")
    else:
        unavail_dims.append("temporal_proximity")
    if trajectory_score is not None:
        avail_dims.append("trajectory_consistency")
    else:
        unavail_dims.append("trajectory_consistency")

    ev_detail = CandidateEvidenceDetail(
        spill=SpillEvidenceSummary(spill_id="spill-001", detection_confidence=0.85),
        environment=EnvironmentalEvidenceSummary(),
        source=SourceEvidenceSummary(
            source_point_lon=9.5,
            source_point_lat=43.2,
            source_uncertainty_radius_km=r_zone_km,
            current_fallback_applied=current_fallback,
            shoreline_terminated=shoreline_terminated,
        ),
        ais=AisEvidenceSummary(mmsi=mmsi, vessel_name=name, vessel_type=vessel_type),
        spatial=SpatialEvidenceSummary(min_distance_to_center_km=d_center_km),
        temporal=TemporalEvidenceSummary(time_offset_from_source_hours=delta_t_hours),
        kinematic=KinematicEvidenceSummary(reported_sog_knots=10.0),
        data_quality=DataQualityEvidenceSummary(
            observed_positions_count=obs_count,
            sparse_track=(obs_count <= 2),
            is_real_observation=is_real,
            data_source_type=data_source_type,
        ),
    )

    norm_ev = NormalizedEvidenceProfile(
        spatial_proximity=spatial_score,
        temporal_proximity=temporal_score,
        trajectory_alignment=trajectory_score,
        source_zone_membership=(d_center_km is not None and d_center_km <= r_zone_km),
        environmental_compatibility="FAVORABLE_DETECTION_WINDOW",
        trajectory_quality="SUFFICIENT" if obs_count >= 3 else "SPARSE",
    )

    avail_prof = EvidenceAvailabilityProfile(
        available_dimensions=avail_dims,
        unavailable_dimensions=unavail_dims,
        missing_reasons={"trajectory_consistency": "insufficient_observations"} if trajectory_score is None else {},
    )

    unc_prof = MultiSourceUncertainty(
        source_uncertainty_km=r_zone_km,
        source_fallback_applied=(current_fallback or shoreline_terminated),
        trajectory_uncertainty="sparse_track" if obs_count <= 2 else "sufficient",
    )

    prov_prof = EvidenceProvenanceRecord(
        source_assets={"D3": "source-001", "E1": "cand-001"},
        data_source_type=data_source_type,
        is_real_observation=is_real,
        fallback_flags={"current_fallback": current_fallback, "shoreline_terminated": shoreline_terminated},
    )

    warnings: list[str] = []
    if obs_count <= 2:
        warnings.append("Sparse AIS trajectory (<= 2 observations).")
    if current_fallback:
        warnings.append("D3 source estimation used wind-only current fallback.")

    return VesselFusedEvidence(
        mmsi=mmsi,
        vessel_name=name,
        candidate_id=candidate_id,
        vessel_id=vessel_id,
        vessel_type=vessel_type,
        input_index=input_index,
        composite_concordance_score=0.70,
        evidence_availability_ratio=len(avail_dims) / 3.0,
        primary_signals=signals,
        behavioral_context=BehavioralContextSummary(total_anomalies_count=n_anomalies),
        forward_drift_cross_check=ForwardDriftCrossCheck(evaluated=fwd_evaluated, min_distance_to_forward_track_km=fwd_dist_km),
        evidence=ev_detail,
        normalized_evidence=norm_ev,
        availability=avail_prof,
        uncertainty=unc_prof,
        provenance=prov_prof,
        warnings=warnings,
        metadata={"zero_fabrication": True, "is_real_observation": is_real, "data_source_type": data_source_type},
    )


def _make_f1_result(
    candidates: list[VesselFusedEvidence],
    inv_id: str = "inv-f2-test",
    spill_id: str = "spill-f2-test",
) -> EvidenceFusionResult:
    return EvidenceFusionResult(
        id=f"ef-{inv_id}",
        investigation_id=inv_id,
        spill_detection_id=spill_id,
        source_estimate_id="source-001",
        candidate_generation_id="cg-001",
        trajectory_analysis_id="ta-001",
        behavioral_intelligence_id="bi-001",
        normalization_reference_radius_km=8.0,
        temporal_scale_hours=2.0,
        nominal_weights={"spatial_proximity": 0.50, "temporal_proximity": 0.25, "trajectory_consistency": 0.25},
        candidate_count=len(candidates),
        fused_candidates=candidates,
        warnings=["Stage F1 test warnings"] if any(c.warnings for c in candidates) else [],
        metadata={"zero_fabrication": True},
    )


class StageF2ScoringAndRankingTests(unittest.TestCase):
    """Hermetic 24-scenario test suite for Stage F2 Candidate Scoring & Ranking."""

    def setUp(self) -> None:
        self.inv_id = "inv-f2-test"
        self.spill_id = "spill-f2-test"
        self.client = TestClient(app)

    # 1. All evidence available
    def test_01_all_evidence_available(self) -> None:
        c = _make_f1_candidate(spatial_score=0.80, temporal_score=0.60, trajectory_score=0.40)
        res = _make_f1_result([c])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(ranking.candidate_count, 1)
        cand = ranking.candidates[0]
        # (0.50*0.80 + 0.25*0.60 + 0.25*0.40) / 1.00 = 0.40 + 0.15 + 0.10 = 0.65
        self.assertAlmostEqual(cand.evidence_consistency_score, 0.65, places=5)
        self.assertEqual(cand.valid_primary_channels, 3)
        self.assertAlmostEqual(cand.evidence_availability_ratio, 1.0, places=5)
        self.assertEqual(cand.available_dimensions, ["spatial", "temporal", "trajectory"])
        self.assertEqual(cand.unavailable_dimensions, [])

    # 2. One evidence dimension missing
    def test_02_one_evidence_dimension_missing(self) -> None:
        c = _make_f1_candidate(spatial_score=0.80, temporal_score=0.60, trajectory_score=None)
        res = _make_f1_result([c])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        cand = ranking.candidates[0]
        # Denominator: 0.50 + 0.25 = 0.75. Numerator: 0.50*0.80 + 0.25*0.60 = 0.55.
        # Score = 0.55 / 0.75 = 11/15 ≈ 0.733333
        self.assertAlmostEqual(cand.evidence_consistency_score, 11.0 / 15.0, places=5)
        self.assertEqual(cand.valid_primary_channels, 2)
        self.assertIn("trajectory", cand.unavailable_dimensions)
        self.assertAlmostEqual(cand.active_weight_sum, 0.75, places=5)

    # 3. Multiple evidence dimensions missing
    def test_03_multiple_evidence_dimensions_missing(self) -> None:
        c = _make_f1_candidate(spatial_score=0.80, temporal_score=None, trajectory_score=None)
        res = _make_f1_result([c])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        cand = ranking.candidates[0]
        # Only spatial available: score = 0.80 / 1.0 = 0.80
        self.assertAlmostEqual(cand.evidence_consistency_score, 0.80, places=5)
        self.assertEqual(cand.valid_primary_channels, 1)
        self.assertAlmostEqual(cand.active_weights["spatial"], 1.0, places=5)

    # 4. All scoring evidence missing
    def test_04_all_scoring_evidence_missing(self) -> None:
        c = _make_f1_candidate(spatial_score=None, temporal_score=None, trajectory_score=None)
        res = _make_f1_result([c])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        cand = ranking.candidates[0]
        # When all channels missing, score is None, NEVER 0.0
        self.assertIsNone(cand.evidence_consistency_score)
        self.assertEqual(cand.valid_primary_channels, 0)
        self.assertEqual(cand.evidence_availability_ratio, 0.0)
        self.assertEqual(len(cand.unavailable_dimensions), 3)

    # 5. Single AIS observation
    def test_05_single_ais_observation(self) -> None:
        c = _make_f1_candidate(obs_count=1, trajectory_score=None)
        res = _make_f1_result([c])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        cand = ranking.candidates[0]
        self.assertEqual(cand.uncertainty_summary.get("trajectory_uncertainty"), "sparse_track")
        self.assertTrue(any("Sparse AIS trajectory" in w for w in cand.warnings))

    # 6. Sparse trajectory (<= 2 observations)
    def test_06_sparse_trajectory(self) -> None:
        c = _make_f1_candidate(obs_count=2, trajectory_score=0.50)
        res = _make_f1_result([c])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        cand = ranking.candidates[0]
        self.assertEqual(cand.uncertainty_summary.get("trajectory_uncertainty"), "sparse_track")

    # 7. Equal scores tie-breaking
    def test_07_equal_scores_tie_breaking(self) -> None:
        c1 = _make_f1_candidate("c1", "v1", spatial_score=0.80, temporal_score=0.80, trajectory_score=0.80, d_center_km=3.0)
        c2 = _make_f1_candidate("c2", "v2", spatial_score=0.80, temporal_score=0.80, trajectory_score=0.80, d_center_km=1.0)
        res = _make_f1_result([c1, c2])

        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        # c2 has smaller spatial discrepancy (1.0 km vs 3.0 km) -> ranks #1
        self.assertEqual(ranking.candidates[0].candidate_id, "c2")
        self.assertEqual(ranking.candidates[0].rank, 1)
        self.assertEqual(ranking.candidates[1].candidate_id, "c1")
        self.assertEqual(ranking.candidates[1].rank, 2)
        self.assertTrue(ranking.candidates[0].ranking_explanation_metadata.get("is_tied_score"))

    # 8. Near-equal scores
    def test_08_near_equal_scores(self) -> None:
        c1 = _make_f1_candidate("c1", "v1", spatial_score=0.800001, temporal_score=0.60, trajectory_score=0.40)
        c2 = _make_f1_candidate("c2", "v2", spatial_score=0.800000, temporal_score=0.60, trajectory_score=0.40)
        res = _make_f1_result([c1, c2])

        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        # Higher score strictly ranks first
        self.assertEqual(ranking.candidates[0].candidate_id, "c1")
        self.assertEqual(ranking.candidates[1].candidate_id, "c2")

    # 9. Candidate with same score but different availability ratio
    def test_09_same_score_different_availability(self) -> None:
        # Candidate 1: 3 channels (score = 0.80)
        c1 = _make_f1_candidate("c1", "v1", spatial_score=0.80, temporal_score=0.80, trajectory_score=0.80)
        # Candidate 2: 1 channel (score = 0.80)
        c2 = _make_f1_candidate("c2", "v2", spatial_score=0.80, temporal_score=None, trajectory_score=None)
        res = _make_f1_result([c2, c1])  # Input order reversed

        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        # c1 has higher evidence availability ratio (1.0 vs 0.333333) -> ranks #1
        self.assertEqual(ranking.candidates[0].candidate_id, "c1")
        self.assertEqual(ranking.candidates[1].candidate_id, "c2")

    # 10. Candidate with same score and availability ratio (temporal discrepancy tie-breaker)
    def test_10_same_score_and_availability_temporal_tie_breaker(self) -> None:
        c1 = _make_f1_candidate("c1", "v1", spatial_score=0.80, temporal_score=0.80, trajectory_score=0.80, d_center_km=2.0, delta_t_hours=2.5)
        c2 = _make_f1_candidate("c2", "v2", spatial_score=0.80, temporal_score=0.80, trajectory_score=0.80, d_center_km=2.0, delta_t_hours=0.5)
        res = _make_f1_result([c1, c2])

        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        # Same distance (2.0 km), c2 has smaller temporal discrepancy (0.5 h vs 2.5 h) -> ranks #1
        self.assertEqual(ranking.candidates[0].candidate_id, "c2")

    # 11. Invalid negative weight
    def test_11_invalid_negative_weight(self) -> None:
        c = _make_f1_candidate()
        res = _make_f1_result([c])
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(CandidateRankingError):
                rank_candidates(res, output_dir=tmpdir, weights={"spatial": -0.50, "temporal": 0.25, "trajectory": 0.25})

    # 12. All weights zero
    def test_12_all_weights_zero(self) -> None:
        c = _make_f1_candidate()
        res = _make_f1_result([c])
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(CandidateRankingError):
                rank_candidates(res, output_dir=tmpdir, weights={"spatial": 0.0, "temporal": 0.0, "trajectory": 0.0})

    # 13. Deterministic ranking
    def test_13_deterministic_ranking(self) -> None:
        c1 = _make_f1_candidate("c1", "v1", spatial_score=0.90, temporal_score=0.60, trajectory_score=0.40)
        c2 = _make_f1_candidate("c2", "v2", spatial_score=0.70, temporal_score=0.80, trajectory_score=0.50)
        res = _make_f1_result([c1, c2])

        with tempfile.TemporaryDirectory() as tmpdir1, tempfile.TemporaryDirectory() as tmpdir2:
            r1, _ = rank_candidates(res, output_dir=tmpdir1, registry=InMemoryAssetRegistry())
            r2, _ = rank_candidates(res, output_dir=tmpdir2, registry=InMemoryAssetRegistry())

        self.assertEqual([c.rank for c in r1.candidates], [c.rank for c in r2.candidates])
        self.assertEqual([c.candidate_id for c in r1.candidates], [c.candidate_id for c in r2.candidates])
        self.assertEqual(
            [c.evidence_consistency_score for c in r1.candidates],
            [c.evidence_consistency_score for c in r2.candidates],
        )

    # 14. Provenance preservation
    def test_14_provenance_preservation(self) -> None:
        c = _make_f1_candidate(is_real=False, data_source_type="curated_historical_reconstruction")
        res = _make_f1_result([c])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, asset = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        cand = ranking.candidates[0]
        self.assertFalse(cand.provenance.get("is_real_observation"))
        self.assertEqual(cand.provenance.get("data_source_type"), "curated_historical_reconstruction")
        self.assertTrue(cand.provenance.get("zero_fabrication"))
        self.assertFalse(ranking.metadata.get("is_real_observation"))

    # 15. Uncertainty preservation
    def test_15_uncertainty_preservation(self) -> None:
        c = _make_f1_candidate(r_zone_km=6.5, current_fallback=True, shoreline_terminated=True)
        res = _make_f1_result([c])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        cand = ranking.candidates[0]
        self.assertEqual(cand.uncertainty_summary.get("source_uncertainty_km"), 6.5)
        self.assertTrue(cand.uncertainty_summary.get("source_fallback_applied"))

    # 16. Curated AIS benchmark (MV ULYSSE vs CSL VIRGINIA)
    def test_16_benchmark_ulysse_vs_virginia(self) -> None:
        # MV ULYSSE: MMSI 228308800; CSL VIRGINIA: MMSI 229986000
        # Both near collision point (3.884 km from D3 source, dt = 0.7592 h)
        cand_u = _make_f1_candidate(
            "cand-228308800", "228308800", mmsi="228308800", name="MV ULYSSE", vessel_type="Ro-Ro Cargo",
            spatial_score=0.74, temporal_score=0.93, trajectory_score=0.88, d_center_km=3.884, delta_t_hours=0.7592, obs_count=1,
        )
        cand_v = _make_f1_candidate(
            "cand-229986000", "229986000", mmsi="229986000", name="CSL VIRGINIA", vessel_type="Container Ship",
            spatial_score=0.74, temporal_score=0.93, trajectory_score=0.88, d_center_km=3.884, delta_t_hours=0.7592, obs_count=1,
        )
        res = _make_f1_result([cand_u, cand_v])

        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(ranking.candidate_count, 2)
        # Scores are identical; tie is broken lexicographically by vessel identifier (228308800 < 229986000)
        self.assertEqual(ranking.candidates[0].candidate_id, "cand-228308800")
        self.assertEqual(ranking.candidates[0].rank, 1)
        self.assertEqual(ranking.candidates[1].candidate_id, "cand-229986000")
        self.assertEqual(ranking.candidates[1].rank, 2)
        # Verify provenance
        self.assertFalse(ranking.candidates[0].provenance.get("is_real_observation"))

    # 17. F1 -> F2 serialization round trip
    def test_17_f1_to_f2_serialization_round_trip(self) -> None:
        c = _make_f1_candidate()
        res = _make_f1_result([c])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, asset = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())
            path = Path(asset.location)
            self.assertTrue(path.exists())
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["id"], ranking.id)
            restored = CandidateRanking.model_validate(raw)
            self.assertEqual(restored.candidate_count, ranking.candidate_count)

    # 18. F2 -> F3 compatibility
    def test_18_f2_to_f3_compatibility(self) -> None:
        c1 = _make_f1_candidate("c1", "v1", spatial_score=0.90, temporal_score=0.80, trajectory_score=0.80)
        c2 = _make_f1_candidate("c2", "v2", spatial_score=0.50, temporal_score=0.40, trajectory_score=0.40)
        res = _make_f1_result([c1, c2])

        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())
            report, report_asset = generate_explainability_report(
                candidate_ranking=ranking, output_dir=tmpdir, registry=InMemoryAssetRegistry()
            )

        self.assertIsInstance(report, ExplainabilityReport)
        self.assertEqual(len(report.candidates), 2)
        # F3 preserves F2 ranks and ordering
        self.assertEqual(report.candidates[0].rank, 1)
        self.assertEqual(report.candidates[0].candidate_id, "c1")

    # 19. No probability language/value
    def test_19_no_probability_language(self) -> None:
        c = _make_f1_candidate(spatial_score=0.99, temporal_score=0.99, trajectory_score=0.99)
        res = _make_f1_result([c])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        dump = ranking.model_dump_json().lower()
        for prohibited in [
            "guilty_vessel", "culprit", "perpetrator", "probability_of_guilt",
            "probability_of_spill", "probability_of_responsibility", "definitely_caused",
        ]:
            self.assertNotIn(prohibited, dump)

    # 20. D1 and E3 zero-weight behavior
    def test_20_d1_and_e3_zero_weight(self) -> None:
        c_baseline = _make_f1_candidate("c1", "v1", spatial_score=0.80, temporal_score=0.60, trajectory_score=0.40, n_anomalies=0, fwd_evaluated=False)
        c_with_anomalies = _make_f1_candidate("c1", "v1", spatial_score=0.80, temporal_score=0.60, trajectory_score=0.40, n_anomalies=15, fwd_evaluated=True, fwd_dist_km=0.1)

        res_base = _make_f1_result([c_baseline])
        res_anom = _make_f1_result([c_with_anomalies])

        with tempfile.TemporaryDirectory() as tmpdir1, tempfile.TemporaryDirectory() as tmpdir2:
            r1, _ = rank_candidates(res_base, output_dir=tmpdir1, registry=InMemoryAssetRegistry())
            r2, _ = rank_candidates(res_anom, output_dir=tmpdir2, registry=InMemoryAssetRegistry())

        # Numerical score must be exactly identical
        self.assertEqual(r1.candidates[0].evidence_consistency_score, r2.candidates[0].evidence_consistency_score)
        self.assertEqual(r2.candidates[0].provenance.get("behavioral_score_contribution"), 0.0)
        self.assertEqual(r2.candidates[0].provenance.get("forward_drift_score_contribution"), 0.0)

    # 21. Empty candidate list
    def test_21_empty_candidate_list(self) -> None:
        res = _make_f1_result([])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(ranking.candidate_count, 0)
        self.assertEqual(len(ranking.candidates), 0)

    # 22. One candidate
    def test_22_one_candidate(self) -> None:
        c = _make_f1_candidate("single-cand", "v1", spatial_score=0.75, temporal_score=0.75, trajectory_score=0.75)
        res = _make_f1_result([c])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(ranking.candidate_count, 1)
        self.assertEqual(ranking.candidates[0].rank, 1)

    # 23. Large candidate set (60 candidates)
    def test_23_large_candidate_set(self) -> None:
        candidates = [
            _make_f1_candidate(
                candidate_id=f"c-{i:03d}",
                vessel_id=f"v-{i:03d}",
                spatial_score=round(0.50 + 0.40 * (i / 60.0), 4),
                temporal_score=0.70,
                trajectory_score=0.60,
                input_index=i,
            )
            for i in range(60)
        ]
        res = _make_f1_result(candidates)
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(ranking.candidate_count, 60)
        # Ranks must be 1 to 60 sequentially
        ranks = [c.rank for c in ranking.candidates]
        self.assertEqual(ranks, list(range(1, 61)))
        # Verify scores are strictly monotonically non-increasing
        scores = [c.evidence_consistency_score for c in ranking.candidates]
        for idx in range(len(scores) - 1):
            self.assertGreaterEqual(scores[idx], scores[idx + 1])

    # 24. Backward compatibility (CandidateRankingResult alias & API alias)
    def test_24_backward_compatibility_and_api(self) -> None:
        # Check alias
        self.assertIs(CandidateRankingResult, CandidateRanking)

        # Check API routes
        registry = InMemoryAssetRegistry()
        inv_id = "inv-compat-test"
        spill_id = "spill-compat-test"

        spill_asset = Asset(
            id=spill_id,
            investigation_id=inv_id,
            type=AssetType.SPILL_GEOMETRY,
            provider="test",
            source="spill_detector",
            location="/tmp/spill.geojson",
            provenance=Provenance(product_id=spill_id, retrieved_at=datetime.now(timezone.utc)),
            metadata={"detection_id": spill_id, "spill_id": spill_id},
        )
        registry._assets[spill_asset.id] = spill_asset
        registry._order.append(spill_asset.id)

        c = _make_f1_candidate()
        f_res = _make_f1_result([c], inv_id=inv_id, spill_id=spill_id)

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "evidence_fusion.json"
            json_path.write_text(f_res.model_dump_json(indent=2), encoding="utf-8")

            fusion_asset = Asset(
                id="ef-compat-asset",
                investigation_id=inv_id,
                type=AssetType.DOCUMENT,
                provider="test",
                source="evidence_fusion_service",
                location=str(json_path),
                provenance=Provenance(
                    product_id="ef-compat-asset",
                    retrieved_at=datetime.now(timezone.utc),
                    extra={"asset_type": "evidence_fusion", "spill_detection_id": spill_id},
                ),
                metadata={"asset_type": "evidence_fusion", "spill_id": spill_id},
            )
            registry._assets[fusion_asset.id] = fusion_asset
            registry._order.append(fusion_asset.id)

            from app.acquisition.registry import default_asset_registry
            default_asset_registry._assets[spill_asset.id] = spill_asset
            default_asset_registry._assets[fusion_asset.id] = fusion_asset

            # Test POST /candidates/rank (the new alias)
            resp = self.client.post(
                f"/api/v1/investigations/{inv_id}/spills/{spill_id}/candidates/rank",
                json={"evidence_fusion_id": "ef-compat-asset"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["candidate_count"], 1)


if __name__ == "__main__":
    unittest.main()
