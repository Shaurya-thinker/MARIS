"""Hermetic tests for MARIS Stage F2 — Candidate Scoring & Ranking.

All tests are completely offline and hermetic (no network, no external calls).

Verifies:
1. Three valid primary channels
2. Correct 0.50 / 0.25 / 0.25 weighting
3. Missing spatial evidence
4. Missing temporal evidence
5. Missing trajectory evidence
6. Dynamic weight renormalization
7. Missing evidence is NOT treated as zero
8. Score remains in [0, 1]
9. Candidates are actually ranked in F2 (sorted descending)
10. Ranking is deterministic
11. Deterministic tie-breaking hierarchy:
    - Higher evidence availability ratio
    - Smaller spatial discrepancy
    - Smaller temporal discrepancy
    - Stable vessel identifier
12. Evidence availability ratio calculation
13. Behavioral evidence does NOT change score
14. D1 forward drift cross-check does NOT change score
15. Candidate identity and provenance preservation
16. Zero-fabrication behavior
17. API success path
18. API failure / insufficient-input path
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.acquisition.registry import InMemoryAssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.main import app
from app.models.asset import Asset
from app.models.candidate_ranking import CandidateRanking, RankedCandidate
from app.models.common import AssetType, Provenance
from app.models.evidence_fusion import (
    BehavioralContextSummary,
    EvidenceFusionResult,
    EvidenceSignal,
    ForwardDriftCrossCheck,
    SignalStatus,
    VesselFusedEvidence,
)
from app.services.candidate_ranking import (
    NOMINAL_WEIGHTS,
    CandidateRankingError,
    calculate_candidate_score,
    extract_discrepancies,
    load_evidence_fusion_from_asset,
    rank_candidates,
)


# ---------------------------------------------------------------------------
# Test Fixtures & Factories
# ---------------------------------------------------------------------------


def _make_fused_candidate(
    candidate_id: str = "cand-001",
    vessel_id: str = "vessel-001",
    mmsi: str | None = "111222333",
    name: str | None = "Test Ship",
    vessel_type: str | None = "tanker",
    input_index: int = 0,
    spatial_score: float | None = 0.80,
    temporal_score: float | None = 0.60,
    trajectory_score: float | None = 0.40,
    d_center_km: float | None = 2.0,
    delta_t_hours: float | None = 1.0,
    n_anomalies: int = 0,
    loitering: bool = False,
    n_gaps: int = 0,
    fwd_evaluated: bool = False,
    fwd_dist_km: float | None = None,
    imo: str | None = None,
) -> VesselFusedEvidence:
    signals = [
        EvidenceSignal(
            channel_name="spatial_proximity",
            status=SignalStatus.VALID if spatial_score is not None else SignalStatus.INSUFFICIENT_DATA,
            score=spatial_score,
            nominal_weight=0.50,
            raw_metrics={"d_center_km": d_center_km} if d_center_km is not None else {},
            rationale="Spatial proximity evaluation.",
        ),
        EvidenceSignal(
            channel_name="temporal_proximity",
            status=SignalStatus.VALID if temporal_score is not None else SignalStatus.INSUFFICIENT_DATA,
            score=temporal_score,
            nominal_weight=0.25,
            raw_metrics={"delta_t_hours": delta_t_hours} if delta_t_hours is not None else {},
            rationale="Temporal proximity evaluation.",
        ),
        EvidenceSignal(
            channel_name="trajectory_consistency",
            status=SignalStatus.VALID if trajectory_score is not None else SignalStatus.INSUFFICIENT_DATA,
            score=trajectory_score,
            nominal_weight=0.25,
            raw_metrics={},
            rationale="Trajectory consistency evaluation.",
        ),
    ]

    valid_signals = [s for s in signals if s.status == SignalStatus.VALID and s.score is not None]
    if valid_signals:
        denom = sum(s.nominal_weight for s in valid_signals)
        num = sum(s.nominal_weight * s.score for s in valid_signals)
        comp = round(num / denom, 6)
    else:
        comp = None

    ratio = round(len(valid_signals) / 3.0, 6)

    b_ctx = BehavioralContextSummary(
        total_anomalies_count=n_anomalies,
        loitering_detected=loitering,
        transmission_gaps_count=n_gaps,
    )
    fwd = ForwardDriftCrossCheck(
        evaluated=fwd_evaluated,
        min_distance_to_forward_track_km=fwd_dist_km,
    )

    meta: dict = {"zero_fabrication": True}
    if imo:
        meta["imo"] = imo

    return VesselFusedEvidence(
        candidate_id=candidate_id,
        vessel_id=vessel_id,
        mmsi=mmsi,
        vessel_name=name,
        vessel_type=vessel_type,
        input_index=input_index,
        composite_concordance_score=comp,
        evidence_availability_ratio=ratio,
        primary_signals=signals,
        behavioral_context=b_ctx,
        forward_drift_cross_check=fwd,
        metadata=meta,
    )


def _make_evidence_fusion_result(
    candidates: list[VesselFusedEvidence] | None = None,
    investigation_id: str = "inv-f2-unit",
    spill_id: str = "spill-f2-unit",
    fusion_id: str = "ef-f2-unit",
) -> EvidenceFusionResult:
    cands = candidates if candidates is not None else [_make_fused_candidate()]
    return EvidenceFusionResult(
        id=fusion_id,
        investigation_id=investigation_id,
        spill_detection_id=spill_id,
        source_estimate_id="se-f2-unit",
        candidate_generation_id="cg-f2-unit",
        trajectory_analysis_id="ta-f2-unit",
        behavioral_intelligence_id="bi-f2-unit",
        normalization_reference_radius_km=10.0,
        temporal_scale_hours=2.0,
        nominal_weights={"spatial_proximity": 0.50, "temporal_proximity": 0.25, "trajectory_consistency": 0.25},
        candidate_count=len(cands),
        fused_candidates=cands,
        metadata={"zero_fabrication": True},
    )


# ---------------------------------------------------------------------------
# Test Suite 1: Three Valid Primary Channels
# ---------------------------------------------------------------------------


class TestThreeValidPrimaryChannels(unittest.TestCase):
    """Verify scoring when all 3 primary channels are valid."""

    def test_three_valid_channels_score_and_weights(self):
        """0.50*0.80 + 0.25*0.60 + 0.25*0.40 = 0.40 + 0.15 + 0.10 = 0.65."""
        cand = _make_fused_candidate(
            spatial_score=0.80,
            temporal_score=0.60,
            trajectory_score=0.40,
        )
        (
            score,
            valid_ch,
            total_ch,
            ratio,
            weights_applied,
            sp_s,
            tp_s,
            tr_s,
        ) = calculate_candidate_score(cand)

        self.assertAlmostEqual(score, 0.65, places=6)
        self.assertEqual(valid_ch, 3)
        self.assertEqual(total_ch, 3)
        self.assertAlmostEqual(ratio, 1.0, places=6)
        self.assertAlmostEqual(weights_applied["spatial"], 0.50, places=6)
        self.assertAlmostEqual(weights_applied["temporal"], 0.25, places=6)
        self.assertAlmostEqual(weights_applied["trajectory"], 0.25, places=6)
        self.assertAlmostEqual(sp_s, 0.80, places=6)
        self.assertAlmostEqual(tp_s, 0.60, places=6)
        self.assertAlmostEqual(tr_s, 0.40, places=6)


# ---------------------------------------------------------------------------
# Test Suite 2: Correct Nominal Weighting
# ---------------------------------------------------------------------------


class TestCorrectNominalWeighting(unittest.TestCase):
    """Verify nominal weights configuration and relative impact."""

    def test_nominal_weights_sum_to_one(self):
        """0.50 + 0.25 + 0.25 = 1.00."""
        self.assertAlmostEqual(NOMINAL_WEIGHTS["spatial"], 0.50, places=6)
        self.assertAlmostEqual(NOMINAL_WEIGHTS["temporal"], 0.25, places=6)
        self.assertAlmostEqual(NOMINAL_WEIGHTS["trajectory"], 0.25, places=6)
        self.assertAlmostEqual(sum(NOMINAL_WEIGHTS.values()), 1.00, places=6)

    def test_spatial_change_has_double_impact_of_temporal(self):
        """A 0.2 increase in spatial (+0.10) has double the impact of 0.2 in temporal (+0.05)."""
        base_cand = _make_fused_candidate(spatial_score=0.50, temporal_score=0.50, trajectory_score=0.50)
        spatial_boost = _make_fused_candidate(spatial_score=0.70, temporal_score=0.50, trajectory_score=0.50)
        temporal_boost = _make_fused_candidate(spatial_score=0.50, temporal_score=0.70, trajectory_score=0.50)

        base_score, _, _, _, _, _, _, _ = calculate_candidate_score(base_cand)
        sp_score, _, _, _, _, _, _, _ = calculate_candidate_score(spatial_boost)
        tp_score, _, _, _, _, _, _, _ = calculate_candidate_score(temporal_boost)

        delta_sp = sp_score - base_score
        delta_tp = tp_score - base_score
        self.assertAlmostEqual(delta_sp, 0.10, places=6)
        self.assertAlmostEqual(delta_tp, 0.05, places=6)
        self.assertAlmostEqual(delta_sp, 2.0 * delta_tp, places=6)


# ---------------------------------------------------------------------------
# Test Suite 3: Missing Spatial Evidence
# ---------------------------------------------------------------------------


class TestMissingSpatialEvidence(unittest.TestCase):
    """Verify behavior when spatial channel is missing / unavailable."""

    def test_missing_spatial_renormalizes_across_temporal_and_trajectory(self):
        """When spatial is None, denom = 0.25 + 0.25 = 0.50. Score = (0.25*0.8 + 0.25*0.4)/0.50 = 0.60."""
        cand = _make_fused_candidate(
            spatial_score=None,
            temporal_score=0.80,
            trajectory_score=0.40,
        )
        score, valid_ch, total_ch, ratio, weights, _, _, _ = calculate_candidate_score(cand)

        self.assertAlmostEqual(score, 0.60, places=6)
        self.assertEqual(valid_ch, 2)
        self.assertAlmostEqual(ratio, 2.0 / 3.0, places=6)
        self.assertNotIn("spatial", weights)
        self.assertAlmostEqual(weights["temporal"], 0.50, places=6)
        self.assertAlmostEqual(weights["trajectory"], 0.50, places=6)


# ---------------------------------------------------------------------------
# Test Suite 4: Missing Temporal Evidence
# ---------------------------------------------------------------------------


class TestMissingTemporalEvidence(unittest.TestCase):
    """Verify behavior when temporal channel is missing / unavailable."""

    def test_missing_temporal_renormalizes_across_spatial_and_trajectory(self):
        """When temporal is None, denom = 0.50 + 0.25 = 0.75. Score = (0.50*0.9 + 0.25*0.5)/0.75 = 0.766667."""
        cand = _make_fused_candidate(
            spatial_score=0.90,
            temporal_score=None,
            trajectory_score=0.50,
        )
        score, valid_ch, total_ch, ratio, weights, _, _, _ = calculate_candidate_score(cand)

        expected = (0.50 * 0.90 + 0.25 * 0.50) / 0.75
        self.assertAlmostEqual(score, expected, places=6)
        self.assertEqual(valid_ch, 2)
        self.assertAlmostEqual(weights["spatial"], 0.50 / 0.75, places=6)
        self.assertAlmostEqual(weights["trajectory"], 0.25 / 0.75, places=6)


# ---------------------------------------------------------------------------
# Test Suite 5: Missing Trajectory Evidence
# ---------------------------------------------------------------------------


class TestMissingTrajectoryEvidence(unittest.TestCase):
    """Verify behavior when trajectory channel is missing / unavailable."""

    def test_missing_trajectory_renormalizes_across_spatial_and_temporal(self):
        """When trajectory is None, denom = 0.50 + 0.25 = 0.75. Score = (0.50*0.8 + 0.25*0.6)/0.75 = 0.733333."""
        cand = _make_fused_candidate(
            spatial_score=0.80,
            temporal_score=0.60,
            trajectory_score=None,
        )
        score, valid_ch, total_ch, ratio, weights, _, _, _ = calculate_candidate_score(cand)

        expected = (0.50 * 0.80 + 0.25 * 0.60) / 0.75
        self.assertAlmostEqual(score, round(expected, 6), places=6)
        self.assertEqual(valid_ch, 2)
        self.assertAlmostEqual(weights["spatial"], 0.50 / 0.75, places=6)
        self.assertAlmostEqual(weights["temporal"], 0.25 / 0.75, places=6)


# ---------------------------------------------------------------------------
# Test Suite 6: Dynamic Weight Renormalization
# ---------------------------------------------------------------------------


class TestDynamicWeightRenormalization(unittest.TestCase):
    """Verify single-channel valid cases dynamically renormalize weights to 1.0."""

    def test_only_spatial_valid(self):
        """Single valid spatial channel yields score equal to spatial score."""
        cand = _make_fused_candidate(spatial_score=0.75, temporal_score=None, trajectory_score=None)
        score, valid_ch, _, ratio, weights, _, _, _ = calculate_candidate_score(cand)

        self.assertAlmostEqual(score, 0.75, places=6)
        self.assertEqual(valid_ch, 1)
        self.assertAlmostEqual(ratio, 1.0 / 3.0, places=6)
        self.assertAlmostEqual(weights["spatial"], 1.0, places=6)

    def test_only_temporal_valid(self):
        """Single valid temporal channel yields score equal to temporal score."""
        cand = _make_fused_candidate(spatial_score=None, temporal_score=0.62, trajectory_score=None)
        score, valid_ch, _, ratio, weights, _, _, _ = calculate_candidate_score(cand)

        self.assertAlmostEqual(score, 0.62, places=6)
        self.assertEqual(valid_ch, 1)
        self.assertAlmostEqual(weights["temporal"], 1.0, places=6)


# ---------------------------------------------------------------------------
# Test Suite 7: Missing Evidence is NOT Treated as Zero
# ---------------------------------------------------------------------------


class TestMissingEvidenceNotZero(unittest.TestCase):
    """Missing evidence channels must be excluded from denominator, not treated as score=0."""

    def test_missing_trajectory_vs_zero_trajectory(self):
        """Missing trajectory gives 0.733333; trajectory=0.0 gives 0.55. They must differ."""
        cand_missing = _make_fused_candidate(spatial_score=0.80, temporal_score=0.60, trajectory_score=None)
        cand_zero = _make_fused_candidate(spatial_score=0.80, temporal_score=0.60, trajectory_score=0.0)

        score_missing, _, _, _, _, _, _, _ = calculate_candidate_score(cand_missing)
        score_zero, _, _, _, _, _, _, _ = calculate_candidate_score(cand_zero)

        self.assertAlmostEqual(score_missing, (0.50 * 0.8 + 0.25 * 0.6) / 0.75, places=6)
        self.assertAlmostEqual(score_zero, (0.50 * 0.8 + 0.25 * 0.6 + 0.25 * 0.0) / 1.00, places=6)
        self.assertGreater(score_missing, score_zero)


# ---------------------------------------------------------------------------
# Test Suite 8: Score Remains in [0, 1]
# ---------------------------------------------------------------------------


class TestScoreBounds(unittest.TestCase):
    """Evidence consistency score must always be bounded in [0.0, 1.0] or None."""

    def test_min_bounds(self):
        """All zero scores yield 0.0."""
        cand = _make_fused_candidate(spatial_score=0.0, temporal_score=0.0, trajectory_score=0.0)
        score, _, _, _, _, _, _, _ = calculate_candidate_score(cand)
        self.assertAlmostEqual(score, 0.0, places=6)

    def test_max_bounds(self):
        """All 1.0 scores yield 1.0."""
        cand = _make_fused_candidate(spatial_score=1.0, temporal_score=1.0, trajectory_score=1.0)
        score, _, _, _, _, _, _, _ = calculate_candidate_score(cand)
        self.assertAlmostEqual(score, 1.0, places=6)

    def test_no_valid_channels_yields_none(self):
        """When all channels are missing, score is None."""
        cand = _make_fused_candidate(spatial_score=None, temporal_score=None, trajectory_score=None)
        score, valid_ch, _, ratio, _, _, _, _ = calculate_candidate_score(cand)
        self.assertIsNone(score)
        self.assertEqual(valid_ch, 0)
        self.assertAlmostEqual(ratio, 0.0, places=6)


# ---------------------------------------------------------------------------
# Test Suite 9: Candidates are Actually Ranked in F2
# ---------------------------------------------------------------------------


class TestCandidatesActuallyRanked(unittest.TestCase):
    """F2 sorts candidates by descending score (unlike F1 which preserves input order)."""

    def test_descending_sort_and_ranks(self):
        """Candidates with scores [0.40, 0.90, 0.70] must be ranked as [0.90 (rank 1), 0.70 (rank 2), 0.40 (rank 3)]."""
        c1 = _make_fused_candidate("c1", "v1", spatial_score=0.40, temporal_score=0.40, trajectory_score=0.40, input_index=0)
        c2 = _make_fused_candidate("c2", "v2", spatial_score=0.90, temporal_score=0.90, trajectory_score=0.90, input_index=1)
        c3 = _make_fused_candidate("c3", "v3", spatial_score=0.70, temporal_score=0.70, trajectory_score=0.70, input_index=2)

        fusion_res = _make_evidence_fusion_result([c1, c2, c3])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(fusion_res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(len(ranking.candidates), 3)
        self.assertEqual(ranking.candidates[0].candidate_id, "c2")
        self.assertEqual(ranking.candidates[0].rank, 1)
        self.assertAlmostEqual(ranking.candidates[0].evidence_consistency_score, 0.90, places=6)

        self.assertEqual(ranking.candidates[1].candidate_id, "c3")
        self.assertEqual(ranking.candidates[1].rank, 2)
        self.assertAlmostEqual(ranking.candidates[1].evidence_consistency_score, 0.70, places=6)

        self.assertEqual(ranking.candidates[2].candidate_id, "c1")
        self.assertEqual(ranking.candidates[2].rank, 3)
        self.assertAlmostEqual(ranking.candidates[2].evidence_consistency_score, 0.40, places=6)


# ---------------------------------------------------------------------------
# Test Suite 10: Deterministic Ranking
# ---------------------------------------------------------------------------


class TestDeterministicRanking(unittest.TestCase):
    """Repeated execution on identical inputs must produce identical ranking order and scores."""

    def test_repeated_runs_identical_ranks(self):
        c1 = _make_fused_candidate("c1", "v1", spatial_score=0.85, temporal_score=0.50, trajectory_score=0.30)
        c2 = _make_fused_candidate("c2", "v2", spatial_score=0.85, temporal_score=0.50, trajectory_score=0.30)
        c3 = _make_fused_candidate("c3", "v3", spatial_score=0.60, temporal_score=0.40, trajectory_score=0.20)

        fusion_res = _make_evidence_fusion_result([c1, c2, c3])
        with tempfile.TemporaryDirectory() as tmpdir1:
            r1, _ = rank_candidates(fusion_res, output_dir=tmpdir1, registry=InMemoryAssetRegistry())
        with tempfile.TemporaryDirectory() as tmpdir2:
            r2, _ = rank_candidates(fusion_res, output_dir=tmpdir2, registry=InMemoryAssetRegistry())

        for cand1, cand2 in zip(r1.candidates, r2.candidates):
            self.assertEqual(cand1.rank, cand2.rank)
            self.assertEqual(cand1.candidate_id, cand2.candidate_id)
            self.assertEqual(cand1.evidence_consistency_score, cand2.evidence_consistency_score)


# ---------------------------------------------------------------------------
# Test Suite 11: Deterministic Tie-Breaking
# ---------------------------------------------------------------------------


class TestDeterministicTieBreaking(unittest.TestCase):
    """Verify each tier of the tie-breaking hierarchy."""

    def test_tie_broken_by_evidence_availability_ratio(self):
        """When scores are equal, candidate with higher evidence availability ratio ranks first."""
        # c1: 3 channels valid -> score = (0.5*0.8 + 0.25*0.8 + 0.25*0.8) / 1.0 = 0.80, ratio = 1.0
        c1 = _make_fused_candidate("c1", "v1", spatial_score=0.80, temporal_score=0.80, trajectory_score=0.80)
        # c2: only 2 channels valid -> score = (0.5*0.8 + 0.25*0.8) / 0.75 = 0.80, ratio = 0.666667
        c2 = _make_fused_candidate("c2", "v2", spatial_score=0.80, temporal_score=0.80, trajectory_score=None)

        fusion_res = _make_evidence_fusion_result([c2, c1])  # Input c2 first
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(fusion_res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(ranking.candidates[0].candidate_id, "c1")
        self.assertEqual(ranking.candidates[1].candidate_id, "c2")

    def test_tie_broken_by_spatial_discrepancy(self):
        """When scores and ratios are equal, candidate with smaller spatial discrepancy ranks first."""
        c1 = _make_fused_candidate("c1", "v1", spatial_score=0.80, temporal_score=0.80, trajectory_score=0.80, d_center_km=5.0)
        c2 = _make_fused_candidate("c2", "v2", spatial_score=0.80, temporal_score=0.80, trajectory_score=0.80, d_center_km=1.5)

        fusion_res = _make_evidence_fusion_result([c1, c2])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(fusion_res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(ranking.candidates[0].candidate_id, "c2")  # 1.5 km < 5.0 km
        self.assertEqual(ranking.candidates[1].candidate_id, "c1")

    def test_tie_broken_by_temporal_discrepancy(self):
        """When scores, ratios, and spatial discrepancies are equal, smaller temporal discrepancy ranks first."""
        c1 = _make_fused_candidate(
            "c1", "v1", spatial_score=0.80, temporal_score=0.80, trajectory_score=0.80,
            d_center_km=2.0, delta_t_hours=3.5,
        )
        c2 = _make_fused_candidate(
            "c2", "v2", spatial_score=0.80, temporal_score=0.80, trajectory_score=0.80,
            d_center_km=2.0, delta_t_hours=0.5,
        )

        fusion_res = _make_evidence_fusion_result([c1, c2])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(fusion_res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(ranking.candidates[0].candidate_id, "c2")  # 0.5h < 3.5h
        self.assertEqual(ranking.candidates[1].candidate_id, "c1")

    def test_tie_broken_by_stable_vessel_identifier(self):
        """When all metrics are identical, stable vessel identifier breaks the tie lexicographically."""
        c1 = _make_fused_candidate(
            "c-beta", "vessel-B", spatial_score=0.80, temporal_score=0.80, trajectory_score=0.80,
            d_center_km=2.0, delta_t_hours=1.0,
        )
        c2 = _make_fused_candidate(
            "c-alpha", "vessel-A", spatial_score=0.80, temporal_score=0.80, trajectory_score=0.80,
            d_center_km=2.0, delta_t_hours=1.0,
        )

        fusion_res = _make_evidence_fusion_result([c1, c2])
        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(fusion_res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(ranking.candidates[0].vessel_id, "vessel-A")
        self.assertEqual(ranking.candidates[1].vessel_id, "vessel-B")


# ---------------------------------------------------------------------------
# Test Suite 12: Evidence Availability Ratio
# ---------------------------------------------------------------------------


class TestEvidenceAvailabilityRatio(unittest.TestCase):
    """Verify evidence availability ratio calculation across channel counts."""

    def test_ratio_values(self):
        c3 = _make_fused_candidate(spatial_score=0.8, temporal_score=0.6, trajectory_score=0.4)
        c2 = _make_fused_candidate(spatial_score=0.8, temporal_score=0.6, trajectory_score=None)
        c1 = _make_fused_candidate(spatial_score=0.8, temporal_score=None, trajectory_score=None)
        c0 = _make_fused_candidate(spatial_score=None, temporal_score=None, trajectory_score=None)

        _, _, _, r3, _, _, _, _ = calculate_candidate_score(c3)
        _, _, _, r2, _, _, _, _ = calculate_candidate_score(c2)
        _, _, _, r1, _, _, _, _ = calculate_candidate_score(c1)
        _, _, _, r0, _, _, _, _ = calculate_candidate_score(c0)

        self.assertAlmostEqual(r3, 1.0, places=6)
        self.assertAlmostEqual(r2, 2.0 / 3.0, places=6)
        self.assertAlmostEqual(r1, 1.0 / 3.0, places=6)
        self.assertAlmostEqual(r0, 0.0, places=6)


# ---------------------------------------------------------------------------
# Test Suite 13: Behavioral Evidence Does NOT Change Score
# ---------------------------------------------------------------------------


class TestBehavioralEvidenceInvariance(unittest.TestCase):
    """E3 behavioral anomalies must contribute exactly 0.00 to the score."""

    def test_behavioral_anomalies_do_not_alter_score(self):
        cand_clean = _make_fused_candidate("c-clean", "v1", n_anomalies=0, loitering=False, n_gaps=0)
        cand_anom = _make_fused_candidate("c-anom", "v2", n_anomalies=15, loitering=True, n_gaps=8)

        score_clean, _, _, _, _, _, _, _ = calculate_candidate_score(cand_clean)
        score_anom, _, _, _, _, _, _, _ = calculate_candidate_score(cand_anom)

        self.assertAlmostEqual(score_clean, score_anom, places=6)

    def test_behavioral_context_preserved_in_ranked_candidate(self):
        """E3 behavioral findings are preserved as contextual evidence."""
        cand = _make_fused_candidate("c1", "v1", n_anomalies=5, loitering=True, n_gaps=2)
        fusion_res = _make_evidence_fusion_result([cand])

        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(fusion_res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        rc = ranking.candidates[0]
        self.assertEqual(rc.contextual_evidence.total_anomalies_count, 5)
        self.assertTrue(rc.contextual_evidence.loitering_detected)
        self.assertEqual(rc.contextual_evidence.transmission_gaps_count, 2)
        self.assertEqual(rc.provenance.get("behavioral_score_contribution"), 0.0)


# ---------------------------------------------------------------------------
# Test Suite 14: D1 Forward Drift Cross-Check Does NOT Change Score
# ---------------------------------------------------------------------------


class TestForwardDriftDecoupling(unittest.TestCase):
    """D1 forward drift cross-check must contribute exactly 0.00 to the score."""

    def test_forward_drift_evaluated_vs_not_evaluated_same_score(self):
        c_uneval = _make_fused_candidate("c1", "v1", fwd_evaluated=False, fwd_dist_km=None)
        c_eval = _make_fused_candidate("c2", "v2", fwd_evaluated=True, fwd_dist_km=0.05)

        s_uneval, _, _, _, _, _, _, _ = calculate_candidate_score(c_uneval)
        s_eval, _, _, _, _, _, _, _ = calculate_candidate_score(c_eval)

        self.assertAlmostEqual(s_uneval, s_eval, places=6)

    def test_forward_drift_preserved_in_ranked_candidate(self):
        c_eval = _make_fused_candidate("c1", "v1", fwd_evaluated=True, fwd_dist_km=0.12)
        fusion_res = _make_evidence_fusion_result([c_eval])

        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(fusion_res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        rc = ranking.candidates[0]
        self.assertTrue(rc.drift_cross_check.evaluated)
        self.assertAlmostEqual(rc.drift_cross_check.min_distance_to_forward_track_km, 0.12, places=6)
        self.assertEqual(rc.provenance.get("forward_drift_score_contribution"), 0.0)


# ---------------------------------------------------------------------------
# Test Suite 15: Candidate Identity and Provenance Preservation
# ---------------------------------------------------------------------------


class TestCandidateIdentityAndProvenance(unittest.TestCase):
    """Candidate vessel identities and provenance tracking are strictly maintained."""

    def test_candidate_identity_fields_preserved(self):
        cand = _make_fused_candidate(
            candidate_id="cand-xyz-123",
            vessel_id="vessel-abc-999",
            mmsi="244123456",
            name="Nordic Pioneer",
            vessel_type="Crude Oil Tanker",
            imo="9123456",
        )
        fusion_res = _make_evidence_fusion_result([cand])

        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(fusion_res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        rc = ranking.candidates[0]
        self.assertEqual(rc.candidate_id, "cand-xyz-123")
        self.assertEqual(rc.vessel_id, "vessel-abc-999")
        self.assertEqual(rc.mmsi, "244123456")
        self.assertEqual(rc.name, "Nordic Pioneer")
        self.assertEqual(rc.vessel_name, "Nordic Pioneer")
        self.assertEqual(rc.vessel_type, "Crude Oil Tanker")
        self.assertEqual(rc.imo, "9123456")

    def test_provenance_contains_required_fields(self):
        cand = _make_fused_candidate("c1", "v1")
        fusion_res = _make_evidence_fusion_result([cand])

        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, _ = rank_candidates(fusion_res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        prov = ranking.candidates[0].provenance
        self.assertIn("candidate_id", prov)
        self.assertIn("vessel_id", prov)
        self.assertIn("channel_weights_applied", prov)
        self.assertTrue(prov.get("zero_fabrication"))


# ---------------------------------------------------------------------------
# Test Suite 16: Zero Fabrication Behavior
# ---------------------------------------------------------------------------


class TestZeroFabricationBehavior(unittest.TestCase):
    """Zero-fabrication invariant: no synthetic positions or interpolated tracks."""

    def test_zero_fabrication_flags_present(self):
        cand = _make_fused_candidate("c1", "v1")
        fusion_res = _make_evidence_fusion_result([cand])

        with tempfile.TemporaryDirectory() as tmpdir:
            ranking, asset = rank_candidates(fusion_res, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertTrue(ranking.metadata.get("zero_fabrication"))
        self.assertTrue(ranking.candidates[0].provenance.get("zero_fabrication"))
        self.assertTrue(asset.metadata.get("zero_fabrication"))


# ---------------------------------------------------------------------------
# Test Suite 17: API Success Path
# ---------------------------------------------------------------------------


class TestCandidateRankingAPISuccess(unittest.TestCase):
    """Integration test for API route success path."""

    def setUp(self):
        self.client = TestClient(app)

    def test_api_success_path_with_registered_assets(self):
        registry = default_asset_registry
        inv_id = "inv-api-success-f2"
        spill_id = "spill-api-success-f2"

        # 1. Register spill asset
        spill_asset = Asset(
            id=spill_id,
            investigation_id=inv_id,
            type=AssetType.SPILL_GEOMETRY,
            provider="test",
            source="test",
            location="/nonexistent/spill.geojson",
            metadata={"detection_id": spill_id, "spill_id": spill_id},
        )
        registry._assets[spill_asset.id] = spill_asset
        registry._order.append(spill_asset.id)

        # 2. Register evidence fusion JSON file
        with tempfile.TemporaryDirectory() as tmpdir:
            c1 = _make_fused_candidate("c1", "v1", spatial_score=0.80, temporal_score=0.60, trajectory_score=0.40)
            c2 = _make_fused_candidate("c2", "v2", spatial_score=0.90, temporal_score=0.70, trajectory_score=0.50)
            f_res = _make_evidence_fusion_result([c1, c2], investigation_id=inv_id, spill_id=spill_id)

            json_path = Path(tmpdir) / "evidence_fusion.json"
            json_path.write_text(f_res.model_dump_json(indent=2), encoding="utf-8")

            fusion_asset = Asset(
                id="ef-api-test",
                investigation_id=inv_id,
                type=AssetType.DOCUMENT,
                provider="test",
                source="evidence_fusion_service",
                location=str(json_path),
                provenance=Provenance(
                    product_id="ef-api-test",
                    retrieved_at=datetime.now(timezone.utc),
                    extra={"asset_type": "evidence_fusion", "spill_detection_id": spill_id},
                ),
                metadata={"asset_type": "evidence_fusion", "spill_id": spill_id},
            )
            registry._assets[fusion_asset.id] = fusion_asset
            registry._order.append(fusion_asset.id)

            response = self.client.post(
                f"/api/v1/investigations/{inv_id}/spills/{spill_id}/candidate-ranking",
                json={"evidence_fusion_id": "ef-api-test"},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["candidate_count"], 2)
        self.assertEqual(len(data["candidates"]), 2)
        # c2 had higher scores (0.9, 0.7, 0.5) than c1 (0.8, 0.6, 0.4), so c2 must be rank 1
        self.assertEqual(data["candidates"][0]["candidate_id"], "c2")
        self.assertEqual(data["candidates"][0]["rank"], 1)
        self.assertEqual(data["candidates"][1]["candidate_id"], "c1")
        self.assertEqual(data["candidates"][1]["rank"], 2)


# ---------------------------------------------------------------------------
# Test Suite 18: API Failure / Insufficient Input Path
# ---------------------------------------------------------------------------


class TestCandidateRankingAPIFailure(unittest.TestCase):
    """Integration test for API failure and error paths."""

    def setUp(self):
        self.client = TestClient(app)

    def test_missing_spill_returns_404(self):
        """POST to candidate-ranking with nonexistent spill returns 404."""
        response = self.client.post(
            "/api/v1/investigations/inv-missing/spills/spill-missing/candidate-ranking",
            json={},
        )
        self.assertEqual(response.status_code, 404)

    def test_missing_evidence_fusion_asset_returns_404(self):
        """POST with valid spill but nonexistent evidence_fusion_id returns 404."""
        registry = default_asset_registry
        inv_id = "inv-f2-err"
        spill_id = "spill-f2-err"

        spill_asset = Asset(
            id=spill_id,
            investigation_id=inv_id,
            type=AssetType.SPILL_GEOMETRY,
            provider="test",
            source="test",
            location="/nonexistent/path",
            metadata={"spill_id": spill_id},
        )
        registry._assets[spill_asset.id] = spill_asset
        registry._order.append(spill_asset.id)

        response = self.client.post(
            f"/api/v1/investigations/{inv_id}/spills/{spill_id}/candidate-ranking",
            json={"evidence_fusion_id": "ef-nonexistent"},
        )
        self.assertEqual(response.status_code, 404)

    def test_unregistered_fusion_auto_discovery_fails_404(self):
        """POST with no fusion_id when no fusion asset exists returns 404."""
        registry = default_asset_registry
        inv_id = "inv-f2-no-fusion"
        spill_id = "spill-f2-no-fusion"

        spill_asset = Asset(
            id=spill_id,
            investigation_id=inv_id,
            type=AssetType.SPILL_GEOMETRY,
            provider="test",
            source="test",
            location="/nonexistent/path",
            metadata={"spill_id": spill_id},
        )
        registry._assets[spill_asset.id] = spill_asset
        registry._order.append(spill_asset.id)

        response = self.client.post(
            f"/api/v1/investigations/{inv_id}/spills/{spill_id}/candidate-ranking",
            json={},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
