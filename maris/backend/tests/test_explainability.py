"""Hermetic tests for MARIS Stage F3 — Explainability & Uncertainty.

All tests are completely offline and hermetic (no network, no external calls).

Verifies all 32 required conditions:
1. F2 candidate ranking order is preserved.
2. F2 rank values are preserved.
3. F2 evidence_consistency_score is preserved exactly.
4. HIGH band (score >= 0.75).
5. MODERATE band (0.50 <= score < 0.75).
6. LOW band (score < 0.50).
7. score=None / no valid primary evidence behavior (INSUFFICIENT_DATA).
8. Spatial explanation with discrepancy.
9. Temporal explanation with discrepancy.
10. Trajectory explanation.
11. Unavailable/insufficient trajectory explanation.
12. Missing evidence is not described as negative evidence.
13. Evidence availability ratio is preserved.
14. 3/3 availability wording ("All three primary evidence channels were available.").
15. 2/3 availability wording ("Two of three primary evidence channels were available.").
16. 1/3 availability wording ("One of three primary evidence channels was available.").
17. E3 behavioral context does not alter score.
18. E3 behavioral context is explicitly labeled contextual.
19. D1 cross-check does not alter score.
20. D1 cross-check is explicitly labeled non-additive/contextual.
21. Source zone is described as analytical search envelope.
22. No unsupported confidence percentage is generated.
23. No unsupported confidence interval is generated.
24. Deterministic repeated execution.
25. Candidate identity preservation.
26. Provenance preservation.
27. Zero-fabrication invariant.
28. Scientific disclaimer presence.
29. API success path.
30. API missing-F2/error path.
31. Artifact registration.
32. Limitations are generated only when applicable.
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.acquisition.registry import InMemoryAssetRegistry, default_asset_registry
from app.main import app
from app.models.asset import Asset
from app.models.candidate_ranking import CandidateRanking, RankedCandidate
from app.models.common import AssetType, Provenance
from app.models.evidence_fusion import (
    BehavioralContextSummary,
    ForwardDriftCrossCheck,
)
from app.models.explainability import (
    SCIENTIFIC_DISCLAIMER,
    DriftCrossCheckStatus,
    EvidenceConsistencyLevel,
    ExplainabilityReport,
)
from app.services.explainability import (
    ExplainabilityError,
    build_candidate_limitations,
    determine_consistency_level,
    explain_behavioral_context,
    explain_drift_cross_check,
    explain_spatial_channel,
    explain_temporal_channel,
    explain_trajectory_channel,
    format_evidence_availability,
    generate_explainability_report,
    load_candidate_ranking_from_asset,
)


# ---------------------------------------------------------------------------
# Test Fixtures & Factories
# ---------------------------------------------------------------------------


def _make_ranked_candidate(
    candidate_id: str = "cand-001",
    vessel_id: str = "vessel-001",
    rank: int = 1,
    mmsi: str | None = "111222333",
    name: str | None = "Nordic Star",
    vessel_type: str | None = "Crude Oil Tanker",
    score: float | None = 0.85,
    spatial_score: float | None = 0.90,
    temporal_score: float | None = 0.80,
    trajectory_score: float | None = 0.80,
    valid_channels: int = 3,
    ratio: float = 1.0,
    spatial_disp_km: float | None = 1.2,
    temporal_disp_hours: float | None = 0.5,
    n_anomalies: int = 0,
    loitering: bool = False,
    n_gaps: int = 0,
    gaps_spanning: int = 0,
    fwd_evaluated: bool = False,
    fwd_dist_km: float | None = None,
    imo: str | None = "9123456",
) -> RankedCandidate:
    b_ctx = BehavioralContextSummary(
        total_anomalies_count=n_anomalies,
        loitering_detected=loitering,
        transmission_gaps_count=n_gaps,
        gaps_spanning_zone_count=gaps_spanning,
    )
    fwd = ForwardDriftCrossCheck(
        evaluated=fwd_evaluated,
        min_distance_to_forward_track_km=fwd_dist_km,
    )
    prov = {
        "candidate_id": candidate_id,
        "vessel_id": vessel_id,
        "spatial_discrepancy_km": spatial_disp_km,
        "temporal_discrepancy_hours": temporal_disp_hours,
        "zero_fabrication": True,
    }
    return RankedCandidate(
        rank=rank,
        vessel_id=vessel_id,
        candidate_id=candidate_id,
        mmsi=mmsi,
        imo=imo,
        name=name,
        vessel_type=vessel_type,
        evidence_consistency_score=score,
        spatial_score=spatial_score,
        temporal_score=temporal_score,
        trajectory_score=trajectory_score,
        valid_primary_channels=valid_channels,
        total_primary_channels=3,
        evidence_availability_ratio=ratio,
        contextual_evidence=b_ctx,
        drift_cross_check=fwd,
        limitations=[],
        provenance=prov,
    )


def _make_candidate_ranking(
    candidates: list[RankedCandidate] | None = None,
    investigation_id: str = "inv-f3-test",
    spill_id: str = "spill-f3-test",
    ranking_id: str = "ranking-f3-test",
) -> CandidateRanking:
    cands = candidates if candidates is not None else [_make_ranked_candidate()]
    return CandidateRanking(
        id=ranking_id,
        investigation_id=investigation_id,
        spill_id=spill_id,
        evidence_fusion_id="ef-f3-test",
        generated_at=datetime.now(timezone.utc),
        methodology_version="F2-1.0.0",
        nominal_weights={"spatial": 0.50, "temporal": 0.25, "trajectory": 0.25},
        candidate_count=len(cands),
        candidates=cands,
        metadata={"zero_fabrication": True},
    )


# ---------------------------------------------------------------------------
# Comprehensive Test Suite (32 tests)
# ---------------------------------------------------------------------------


class TestStageF3Explainability(unittest.TestCase):
    """Hermetic unit and integration tests covering all 32 Stage F3 specifications."""

    def setUp(self):
        self.client = TestClient(app)

    # 1. F2 candidate ranking order is preserved
    def test_01_f2_candidate_ranking_order_preserved(self):
        c1 = _make_ranked_candidate("c-first", "v-1", rank=1, score=0.90)
        c2 = _make_ranked_candidate("c-second", "v-2", rank=2, score=0.60)
        c3 = _make_ranked_candidate("c-third", "v-3", rank=3, score=0.30)
        ranking = _make_candidate_ranking([c1, c2, c3])

        with tempfile.TemporaryDirectory() as tmpdir:
            report, _ = generate_explainability_report(ranking, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual([c.candidate_id for c in report.candidates], ["c-first", "c-second", "c-third"])

    # 2. F2 rank values are preserved
    def test_02_f2_rank_values_preserved(self):
        c1 = _make_ranked_candidate("c1", "v1", rank=1, score=0.90)
        c2 = _make_ranked_candidate("c2", "v2", rank=2, score=0.60)
        ranking = _make_candidate_ranking([c1, c2])

        with tempfile.TemporaryDirectory() as tmpdir:
            report, _ = generate_explainability_report(ranking, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(report.candidates[0].rank, 1)
        self.assertEqual(report.candidates[1].rank, 2)

    # 3. F2 evidence_consistency_score is preserved exactly
    def test_03_f2_evidence_consistency_score_preserved_exactly(self):
        score_val = 0.733333
        c = _make_ranked_candidate(score=score_val)
        ranking = _make_candidate_ranking([c])

        with tempfile.TemporaryDirectory() as tmpdir:
            report, _ = generate_explainability_report(ranking, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(report.candidates[0].evidence_consistency_score, score_val)

    # 4. HIGH band (score >= 0.75)
    def test_04_high_consistency_band(self):
        self.assertEqual(determine_consistency_level(0.75), EvidenceConsistencyLevel.HIGH)
        self.assertEqual(determine_consistency_level(0.92), EvidenceConsistencyLevel.HIGH)
        self.assertEqual(determine_consistency_level(1.00), EvidenceConsistencyLevel.HIGH)

    # 5. MODERATE band (0.50 <= score < 0.75)
    def test_05_moderate_consistency_band(self):
        self.assertEqual(determine_consistency_level(0.50), EvidenceConsistencyLevel.MODERATE)
        self.assertEqual(determine_consistency_level(0.65), EvidenceConsistencyLevel.MODERATE)
        self.assertEqual(determine_consistency_level(0.749999), EvidenceConsistencyLevel.MODERATE)

    # 6. LOW band (score < 0.50)
    def test_06_low_consistency_band(self):
        self.assertEqual(determine_consistency_level(0.499999), EvidenceConsistencyLevel.LOW)
        self.assertEqual(determine_consistency_level(0.20), EvidenceConsistencyLevel.LOW)
        self.assertEqual(determine_consistency_level(0.00), EvidenceConsistencyLevel.LOW)

    # 7. score=None / no valid primary evidence behavior
    def test_07_score_none_insufficient_data_band(self):
        self.assertEqual(determine_consistency_level(None), EvidenceConsistencyLevel.INSUFFICIENT_DATA)
        c = _make_ranked_candidate(score=None, valid_channels=0, ratio=0.0)
        ranking = _make_candidate_ranking([c])

        with tempfile.TemporaryDirectory() as tmpdir:
            report, _ = generate_explainability_report(ranking, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(report.candidates[0].evidence_consistency_level, EvidenceConsistencyLevel.INSUFFICIENT_DATA)

    # 8. Spatial explanation
    def test_08_spatial_explanation(self):
        expl = explain_spatial_channel(0.85, 1.45)
        self.assertIn("HIGH consistency", expl)
        self.assertIn("1.45 km", expl)

    # 9. Temporal explanation
    def test_09_temporal_explanation(self):
        expl = explain_temporal_channel(0.60, 2.10)
        self.assertIn("MODERATE consistency", expl)
        self.assertIn("2.10 hours", expl)

    # 10. Trajectory explanation
    def test_10_trajectory_explanation(self):
        expl = explain_trajectory_channel(0.45)
        self.assertIn("LOW consistency", expl)
        self.assertIn("centerline proximity", expl)

    # 11. Unavailable/insufficient trajectory explanation
    def test_11_unavailable_insufficient_trajectory_explanation(self):
        expl = explain_trajectory_channel(None)
        self.assertIn("Trajectory evidence is insufficient", expl)
        self.assertIn("fewer than two genuine AIS observations were available", expl)

    # 12. Missing evidence is not described as negative evidence
    def test_12_missing_evidence_not_described_as_negative_evidence(self):
        expl_traj = explain_trajectory_channel(None)
        expl_spat = explain_spatial_channel(None, None)
        expl_temp = explain_temporal_channel(None, None)

        for text in [expl_traj, expl_spat, expl_temp]:
            self.assertNotIn("no match", text.lower())
            self.assertNotIn("negative match", text.lower())
            self.assertNotIn("failed", text.lower())

    # 13. Evidence availability ratio is preserved
    def test_13_evidence_availability_ratio_preserved(self):
        c = _make_ranked_candidate(ratio=0.666667, valid_channels=2)
        ranking = _make_candidate_ranking([c])

        with tempfile.TemporaryDirectory() as tmpdir:
            report, _ = generate_explainability_report(ranking, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertAlmostEqual(report.candidates[0].evidence_availability_ratio, 0.666667, places=6)

    # 14. 3/3 availability wording
    def test_14_availability_wording_three_of_three(self):
        text, band = format_evidence_availability(3, 3)
        self.assertEqual(text, "All three primary evidence channels were available.")
        self.assertEqual(band, "HIGH evidence availability")

    # 15. 2/3 availability wording
    def test_15_availability_wording_two_of_three(self):
        text, band = format_evidence_availability(2, 3)
        self.assertEqual(text, "Two of three primary evidence channels were available.")
        self.assertEqual(band, "MODERATE evidence availability")

    # 16. 1/3 availability wording
    def test_16_availability_wording_one_of_three(self):
        text, band = format_evidence_availability(1, 3)
        self.assertEqual(text, "One of three primary evidence channels was available.")
        self.assertEqual(band, "LIMITED evidence availability")

    # 17. E3 behavioral context does not alter score
    def test_17_behavioral_context_does_not_alter_score(self):
        c_clean = _make_ranked_candidate("c1", "v1", score=0.70, n_anomalies=0)
        c_anom = _make_ranked_candidate("c2", "v2", score=0.70, n_anomalies=12, loitering=True, n_gaps=4)
        ranking = _make_candidate_ranking([c_clean, c_anom])

        with tempfile.TemporaryDirectory() as tmpdir:
            report, _ = generate_explainability_report(ranking, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(report.candidates[0].evidence_consistency_score, report.candidates[1].evidence_consistency_score)

    # 18. E3 behavioral context is explicitly labeled contextual
    def test_18_behavioral_context_explicitly_labeled_contextual(self):
        c = _make_ranked_candidate(n_anomalies=2, loitering=True)
        b_expl = explain_behavioral_context(c)
        self.assertEqual(b_expl.label, "Contextual behavioral observations")
        self.assertIn("do not contribute numerically", b_expl.numerical_contribution_statement)

    # 19. D1 cross-check does not alter score
    def test_19_drift_cross_check_does_not_alter_score(self):
        c_none = _make_ranked_candidate("c1", "v1", score=0.80, fwd_evaluated=False)
        c_close = _make_ranked_candidate("c2", "v2", score=0.80, fwd_evaluated=True, fwd_dist_km=0.5)
        ranking = _make_candidate_ranking([c_none, c_close])

        with tempfile.TemporaryDirectory() as tmpdir:
            report, _ = generate_explainability_report(ranking, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(report.candidates[0].evidence_consistency_score, report.candidates[1].evidence_consistency_score)

    # 20. D1 cross-check is explicitly labeled non-additive/contextual
    def test_20_drift_cross_check_labeled_non_additive(self):
        c = _make_ranked_candidate(fwd_evaluated=True, fwd_dist_km=1.2)
        d_expl = explain_drift_cross_check(c)
        self.assertEqual(d_expl.label, "Forward drift cross-check")
        self.assertIn("non-additive contextual evidence", d_expl.explanation)
        self.assertIn("does not modify the consistency score", d_expl.non_additive_statement)

    # 21. Source zone is described as analytical search envelope
    def test_21_source_zone_described_as_analytical_search_envelope(self):
        c = _make_ranked_candidate()
        limits = build_candidate_limitations(c)
        self.assertTrue(any("analytical search envelope, not a statistical confidence region" in l for l in limits))

    # 22. No unsupported confidence percentage is generated
    def test_22_no_unsupported_confidence_percentage(self):
        ranking = _make_candidate_ranking()
        with tempfile.TemporaryDirectory() as tmpdir:
            report, _ = generate_explainability_report(ranking, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        dump = report.model_dump_json()
        self.assertNotIn("95%", dump)
        self.assertNotIn("87%", dump)
        self.assertNotIn("confidence percentage", dump.lower())

    # 23. No unsupported confidence interval is generated
    def test_23_no_unsupported_confidence_interval(self):
        ranking = _make_candidate_ranking()
        with tempfile.TemporaryDirectory() as tmpdir:
            report, _ = generate_explainability_report(ranking, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        dump = report.model_dump_json()
        self.assertNotIn("confidence interval", dump.lower())
        self.assertNotIn("probability distribution", dump.lower())

    # 24. Deterministic repeated execution
    def test_24_deterministic_repeated_execution(self):
        c1 = _make_ranked_candidate("c1", "v1", score=0.85)
        c2 = _make_ranked_candidate("c2", "v2", score=0.60)
        ranking = _make_candidate_ranking([c1, c2])

        with tempfile.TemporaryDirectory() as tmpdir1:
            r1, _ = generate_explainability_report(ranking, output_dir=tmpdir1, registry=InMemoryAssetRegistry())
        with tempfile.TemporaryDirectory() as tmpdir2:
            r2, _ = generate_explainability_report(ranking, output_dir=tmpdir2, registry=InMemoryAssetRegistry())

        for exp1, exp2 in zip(r1.candidates, r2.candidates):
            self.assertEqual(exp1.rank, exp2.rank)
            self.assertEqual(exp1.evidence_consistency_level, exp2.evidence_consistency_level)
            self.assertEqual(exp1.spatial_explanation, exp2.spatial_explanation)
            self.assertEqual(exp1.temporal_explanation, exp2.temporal_explanation)
            self.assertEqual(exp1.trajectory_explanation, exp2.trajectory_explanation)
            self.assertEqual(exp1.limitations, exp2.limitations)

    # 25. Candidate identity preservation
    def test_25_candidate_identity_preservation(self):
        c = _make_ranked_candidate(
            candidate_id="c-99", vessel_id="v-99", mmsi="123456789",
            name="Sea Explorer", vessel_type="Tanker", imo="9988776",
        )
        ranking = _make_candidate_ranking([c])

        with tempfile.TemporaryDirectory() as tmpdir:
            report, _ = generate_explainability_report(ranking, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        cand_exp = report.candidates[0]
        self.assertEqual(cand_exp.candidate_id, "c-99")
        self.assertEqual(cand_exp.vessel_id, "v-99")
        self.assertEqual(cand_exp.mmsi, "123456789")
        self.assertEqual(cand_exp.name, "Sea Explorer")
        self.assertEqual(cand_exp.vessel_name, "Sea Explorer")
        self.assertEqual(cand_exp.vessel_type, "Tanker")
        self.assertEqual(cand_exp.imo, "9988776")

    # 26. Provenance preservation
    def test_26_provenance_preservation(self):
        c = _make_ranked_candidate("c1", "v1", spatial_disp_km=3.14, temporal_disp_hours=1.5)
        ranking = _make_candidate_ranking([c])

        with tempfile.TemporaryDirectory() as tmpdir:
            report, _ = generate_explainability_report(ranking, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        prov = report.candidates[0].provenance
        self.assertEqual(prov.get("spatial_discrepancy_km"), 3.14)
        self.assertEqual(prov.get("temporal_discrepancy_hours"), 1.5)
        self.assertEqual(prov.get("candidate_ranking_id"), ranking.id)

    # 27. Zero-fabrication invariant
    def test_27_zero_fabrication_invariant(self):
        c = _make_ranked_candidate()
        ranking = _make_candidate_ranking([c])

        with tempfile.TemporaryDirectory() as tmpdir:
            report, asset = generate_explainability_report(ranking, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertTrue(report.metadata.get("zero_fabrication"))
        self.assertTrue(report.candidates[0].provenance.get("zero_fabrication"))
        self.assertTrue(asset.metadata.get("zero_fabrication"))

    # 28. Scientific disclaimer presence
    def test_28_scientific_disclaimer_presence(self):
        c = _make_ranked_candidate()
        ranking = _make_candidate_ranking([c])

        with tempfile.TemporaryDirectory() as tmpdir:
            report, _ = generate_explainability_report(ranking, output_dir=tmpdir, registry=InMemoryAssetRegistry())

        self.assertEqual(report.scientific_disclaimer, SCIENTIFIC_DISCLAIMER)
        self.assertEqual(report.candidates[0].scientific_disclaimer, SCIENTIFIC_DISCLAIMER)
        self.assertIn("does not establish causation, legal responsibility", report.scientific_disclaimer)

    # 29. API success path
    def test_29_api_success_path(self):
        registry = default_asset_registry
        inv_id = "inv-f3-api-29"
        spill_id = "spill-f3-api-29"

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

        with tempfile.TemporaryDirectory() as tmpdir:
            c1 = _make_ranked_candidate("c1", "v1", score=0.88)
            ranking = _make_candidate_ranking([c1], investigation_id=inv_id, spill_id=spill_id)

            json_path = Path(tmpdir) / "candidate_ranking.json"
            json_path.write_text(ranking.model_dump_json(indent=2), encoding="utf-8")

            ranking_asset = Asset(
                id="cr-api-29",
                investigation_id=inv_id,
                type=AssetType.DOCUMENT,
                provider="test",
                source="candidate_ranking_service",
                location=str(json_path),
                provenance=Provenance(
                    product_id="cr-api-29",
                    retrieved_at=datetime.now(timezone.utc),
                    extra={"asset_type": "candidate_ranking", "spill_detection_id": spill_id},
                ),
                metadata={"asset_type": "candidate_ranking", "spill_id": spill_id},
            )
            registry._assets[ranking_asset.id] = ranking_asset
            registry._order.append(ranking_asset.id)

            response = self.client.post(
                f"/api/v1/investigations/{inv_id}/spills/{spill_id}/explainability",
                json={"candidate_ranking_id": "cr-api-29"},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["candidate_count"], 1)
        self.assertEqual(data["candidates"][0]["evidence_consistency_level"], "HIGH")

    # 30. API missing-F2/error path
    def test_30_api_missing_f2_error_path(self):
        # Missing spill -> 404
        r1 = self.client.post(
            "/api/v1/investigations/inv-404/spills/spill-404/explainability",
            json={},
        )
        self.assertEqual(r1.status_code, 404)

        # Valid spill, missing ranking asset -> 404
        registry = default_asset_registry
        spill_asset = Asset(
            id="spill-30-err",
            investigation_id="inv-30-err",
            type=AssetType.SPILL_GEOMETRY,
            provider="test",
            source="test",
            location="/nonexistent/path",
            metadata={"spill_id": "spill-30-err"},
        )
        registry._assets[spill_asset.id] = spill_asset
        registry._order.append(spill_asset.id)

        r2 = self.client.post(
            "/api/v1/investigations/inv-30-err/spills/spill-30-err/explainability",
            json={"candidate_ranking_id": "nonexistent-cr"},
        )
        self.assertEqual(r2.status_code, 404)

    # 31. Artifact registration
    def test_31_artifact_registration(self):
        ranking = _make_candidate_ranking()
        with tempfile.TemporaryDirectory() as tmpdir:
            report, asset = generate_explainability_report(
                ranking, output_dir=tmpdir, registry=InMemoryAssetRegistry()
            )

        self.assertIsNotNone(report.asset_id)
        self.assertEqual(asset.id, report.asset_id)
        self.assertEqual(asset.type, AssetType.DOCUMENT)
        self.assertEqual(asset.metadata.get("asset_type"), "explainability_report")

    # 32. Limitations are generated only when applicable
    def test_32_limitations_generated_only_when_applicable(self):
        # 1. Full 3 channels, trajectory valid, drift evaluated -> no missing channel limitations
        c_full = _make_ranked_candidate(valid_channels=3, trajectory_score=0.8, fwd_evaluated=True)
        limits_full = build_candidate_limitations(c_full)
        self.assertFalse(any("primary evidence channels were available" in l for l in limits_full))
        self.assertFalse(any("AIS trajectory evidence was insufficient" in l for l in limits_full))
        self.assertFalse(any("Forward drift cross-check was unavailable" in l for l in limits_full))

        # 2. 2 channels available, trajectory missing, drift unavailable
        c_part = _make_ranked_candidate(valid_channels=2, trajectory_score=None, fwd_evaluated=False)
        limits_part = build_candidate_limitations(c_part)
        self.assertTrue(any("Only 2 of 3 primary evidence channels were available" in l for l in limits_part))
        self.assertTrue(any("AIS trajectory evidence was insufficient" in l for l in limits_part))
        self.assertTrue(any("Forward drift cross-check was unavailable" in l for l in limits_part))


if __name__ == "__main__":
    unittest.main()
