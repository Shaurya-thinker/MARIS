"""MARIS Stage F3 — Explainability & Uncertainty Service.

Consumes Stage F2 CandidateRanking and generates a deterministic, investigator-readable
ExplainabilityReport that qualifies physical evidence consistency, transparently explains
primary channel contributions, and establishes scientific boundaries.

CORE ARCHITECTURAL INVARIANTS:
1. F2 IS AUTHORITATIVE:
   - Candidate ordering, rank, evidence_consistency_score, primary scores, and
     evidence availability are strictly preserved from Stage F2.
   - F3 MUST NOT recalculate, modify, or reorder any candidate.

2. EVIDENCE CONSISTENCY LEVEL:
   - Descriptive bands derived strictly from F2 evidence_consistency_score:
     score >= 0.75: HIGH
     score >= 0.50 and < 0.75: MODERATE
     score < 0.50: LOW
     score is None: INSUFFICIENT_DATA

3. UNCERTAINTY COMMUNICATION:
   - Uncertainty is communicated through evidence availability, explicit limitations,
     data coverage state, and model assumptions.
   - NO unsupported confidence percentages, NO unsupported confidence intervals.

4. CONTEXTUAL SIGNALS:
   - E3 behavioral intelligence and D1 forward drift are explicitly labeled contextual
     and non-additive (strictly 0.00 score contribution).

5. SCIENTIFIC DISCLAIMER:
   - Present on the report and every candidate explanation.

6. ZERO FABRICATION:
   - Zero synthetic AIS positions, zero fabricated uncertainty values.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.acquisition.registry import AssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.core.config import settings
from app.models.asset import Asset
from app.models.candidate_ranking import CandidateRanking, RankedCandidate
from app.models.common import AssetType, Provenance
from app.models.explainability import (
    SCIENTIFIC_DISCLAIMER,
    BehavioralExplanation,
    CandidateExplanation,
    DriftCrossCheckExplanation,
    DriftCrossCheckStatus,
    EvidenceConsistencyLevel,
    ExplainabilityReport,
)
from app.services.drift_modelling import _sanitize


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ExplainabilityError(Exception):
    """Raised when Stage F3 explainability generation fails."""


# ---------------------------------------------------------------------------
# Deterministic Explanation Helpers
# ---------------------------------------------------------------------------


def determine_consistency_level(score: float | None) -> EvidenceConsistencyLevel:
    """Map evidence consistency score to deterministic descriptive band."""
    if score is None:
        return EvidenceConsistencyLevel.INSUFFICIENT_DATA
    if score >= 0.75:
        return EvidenceConsistencyLevel.HIGH
    if score >= 0.50:
        return EvidenceConsistencyLevel.MODERATE
    return EvidenceConsistencyLevel.LOW


def format_evidence_availability(valid_count: int, total_count: int = 3) -> tuple[str, str]:
    """Generate factual availability summary and deterministic band.

    Returns:
        (summary_text, availability_band)
    """
    if valid_count >= 3:
        return (
            "All three primary evidence channels were available.",
            "HIGH evidence availability",
        )
    if valid_count == 2:
        return (
            "Two of three primary evidence channels were available.",
            "MODERATE evidence availability",
        )
    if valid_count == 1:
        return (
            "One of three primary evidence channels was available.",
            "LIMITED evidence availability",
        )
    return (
        "No primary evidence channels were available.",
        "NO evidence availability",
    )


def explain_spatial_channel(
    spatial_score: float | None,
    spatial_disp_km: float | None,
) -> str:
    """Explain spatial proximity channel evaluation."""
    if spatial_score is None:
        return "Spatial evidence is unavailable or insufficient; channel excluded from scoring."

    if spatial_score >= 0.75:
        level = "HIGH"
    elif spatial_score >= 0.50:
        level = "MODERATE"
    else:
        level = "LOW"

    if spatial_disp_km is not None:
        return (
            f"Spatial evidence indicates {level} consistency (score: {spatial_score:.2f}). "
            f"Vessel closest point of approach was {spatial_disp_km:.2f} km from estimated source candidate center."
        )
    return f"Spatial evidence indicates {level} consistency (score: {spatial_score:.2f})."


def explain_temporal_channel(
    temporal_score: float | None,
    temporal_disp_hours: float | None,
) -> str:
    """Explain temporal proximity channel evaluation."""
    if temporal_score is None:
        return "Temporal evidence is unavailable or insufficient; channel excluded from scoring."

    if temporal_score >= 0.75:
        level = "HIGH"
    elif temporal_score >= 0.50:
        level = "MODERATE"
    else:
        level = "LOW"

    if temporal_disp_hours is not None:
        return (
            f"Temporal evidence indicates {level} consistency (score: {temporal_score:.2f}). "
            f"Vessel closest approach occurred {temporal_disp_hours:.2f} hours from estimated spill release time."
        )
    return f"Temporal evidence indicates {level} consistency (score: {temporal_score:.2f})."


def explain_trajectory_channel(
    trajectory_score: float | None,
) -> str:
    """Explain trajectory consistency channel evaluation.

    IMPORTANT: Missing evidence is NOT described as a negative match.
    """
    if trajectory_score is None:
        return (
            "Trajectory evidence is insufficient because fewer than two genuine AIS observations were available; "
            "channel excluded from scoring."
        )

    if trajectory_score >= 0.75:
        level = "HIGH"
    elif trajectory_score >= 0.50:
        level = "MODERATE"
    else:
        level = "LOW"

    return (
        f"Trajectory evidence indicates {level} consistency (score: {trajectory_score:.2f}) "
        "based on centerline proximity and observed heading alignment."
    )


def explain_behavioral_context(cand: RankedCandidate) -> BehavioralExplanation:
    """Explain contextual E3 behavioral intelligence findings."""
    ctx = cand.contextual_evidence
    observations: list[str] = []

    if ctx.loitering_detected:
        observations.append("Observed low-speed multi-course-change pattern (loitering) in genuine AIS observations.")
    if ctx.transmission_gaps_count > 0:
        observations.append(f"{ctx.transmission_gaps_count} observable transmission gap(s) detected in AIS observations.")
    if ctx.gaps_spanning_zone_count > 0:
        observations.append(f"{ctx.gaps_spanning_zone_count} transmission gap(s) observed spanning the candidate source zone.")
    if ctx.speed_drop_in_zone:
        observations.append("Observed abrupt speed reduction inside candidate zone.")
    if ctx.speed_surge_near_zone:
        observations.append("Observed speed surge near candidate zone.")
    if ctx.nav_status_mismatch:
        observations.append("Reported navigation status contradicts observed physical kinematics.")
    if ctx.anchor_swing_observed:
        observations.append("Observed positional envelope consistent with anchored or stationary state.")

    if not observations:
        observations.append("No anomalous behavioral patterns observed in genuine AIS observations.")

    return BehavioralExplanation(
        label="Contextual behavioral observations",
        observations=observations,
        numerical_contribution_statement=(
            "Contextual behavioral observations do not contribute numerically to the evidence_consistency_score."
        ),
        raw_summary=ctx,
    )


def explain_drift_cross_check(cand: RankedCandidate) -> DriftCrossCheckExplanation:
    """Explain auxiliary D1 forward drift cross-check."""
    fwd = cand.drift_cross_check

    if not fwd.evaluated:
        return DriftCrossCheckExplanation(
            label="Forward drift cross-check",
            status=DriftCrossCheckStatus.UNAVAILABLE,
            min_distance_km=None,
            explanation=(
                "Forward drift cross-check was unavailable; D1 forward drift simulation was not provided "
                "for this investigation. This does not affect the primary consistency score."
            ),
            non_additive_statement=(
                "Forward drift cross-check is non-additive contextual evidence and does not modify the consistency score."
            ),
            raw_cross_check=fwd,
        )

    dist = fwd.min_distance_to_forward_track_km
    if dist is not None and dist <= 5.0:
        status = DriftCrossCheckStatus.CONSISTENT
        explanation = (
            f"Forward drift cross-check is CONSISTENT. Closest observed AIS position was {dist:.2f} km "
            "from the D1 forward advection track. This is non-additive contextual evidence."
        )
    else:
        status = DriftCrossCheckStatus.NOT_CONSISTENT
        dist_str = f"{dist:.2f} km" if dist is not None else "undetermined distance"
        explanation = (
            f"Forward drift cross-check is NOT_CONSISTENT. Closest observed AIS position was {dist_str} "
            "from the D1 forward advection track. This is non-additive contextual evidence."
        )

    return DriftCrossCheckExplanation(
        label="Forward drift cross-check",
        status=status,
        min_distance_km=dist,
        explanation=explanation,
        non_additive_statement=(
            "Forward drift cross-check is non-additive contextual evidence and does not modify the consistency score."
        ),
        raw_cross_check=fwd,
    )


def build_candidate_limitations(cand: RankedCandidate) -> list[str]:
    """Generate factual, deterministic limitation statements from actual evidence state."""
    limitations: list[str] = []

    # Channel availability limitations
    if cand.valid_primary_channels == 2:
        limitations.append("Only 2 of 3 primary evidence channels were available.")
    elif cand.valid_primary_channels == 1:
        limitations.append("Only 1 of 3 primary evidence channels was available.")
    elif cand.valid_primary_channels == 0:
        limitations.append("No primary evidence channels were available.")

    # Trajectory specific limitation
    if cand.trajectory_score is None:
        limitations.append("AIS trajectory evidence was insufficient.")

    # Drift cross-check limitation
    if not cand.drift_cross_check.evaluated:
        limitations.append("Forward drift cross-check was unavailable.")

    # Inherent documented physical and observational limitations
    limitations.append(
        "AIS observations are discrete reported observations and do not establish continuous vessel position between observations."
    )
    limitations.append(
        "The source zone is an analytical search envelope, not a statistical confidence region."
    )
    limitations.append(
        "Results are subject to the data coverage and model assumptions documented by upstream processing stages."
    )

    return limitations


# ---------------------------------------------------------------------------
# Main Explainability Service Entry Point
# ---------------------------------------------------------------------------


def generate_explainability_report(
    candidate_ranking: CandidateRanking,
    investigation_id: str | None = None,
    spill_id: str | None = None,
    output_dir: str | Path | None = None,
    registry: AssetRegistry | None = None,
) -> tuple[ExplainabilityReport, Asset]:
    """Generate Stage F3 ExplainabilityReport from Stage F2 CandidateRanking.

    INVARIANT: Preserves exact candidate order, ranks, and scores from F2.

    Args:
        candidate_ranking: Authoritative Stage F2 candidate ranking result.
        investigation_id: Optional override for investigation ID.
        spill_id: Optional override for spill ID.
        output_dir: Optional override directory for writing artifact.
        registry: Target AssetRegistry for registering derived DOCUMENT asset.

    Returns:
        (ExplainabilityReport, Asset)

    Raises:
        ExplainabilityError: If candidate_ranking is None or malformed.
    """
    if candidate_ranking is None:
        raise ExplainabilityError("Cannot generate explainability report: candidate_ranking input is None.")

    target_inv = investigation_id or candidate_ranking.investigation_id
    target_spill = spill_id or candidate_ranking.spill_id
    target_registry = registry or default_asset_registry

    now_utc = datetime.now(timezone.utc)
    res_id = f"explainability-{_sanitize(target_spill, 'spill')}-{_sanitize(target_inv, 'inv')}"

    # Generate explanation for each candidate in EXACT F2 order
    explained_candidates: list[CandidateExplanation] = []

    for cand in candidate_ranking.candidates:
        consistency_level = determine_consistency_level(cand.evidence_consistency_score)
        avail_summary, avail_band = format_evidence_availability(
            cand.valid_primary_channels, cand.total_primary_channels
        )

        sp_disp = cand.provenance.get("spatial_discrepancy_km")
        tp_disp = cand.provenance.get("temporal_discrepancy_hours")

        sp_expl = explain_spatial_channel(cand.spatial_score, sp_disp)
        tp_expl = explain_temporal_channel(cand.temporal_score, tp_disp)
        tr_expl = explain_trajectory_channel(cand.trajectory_score)

        b_expl = explain_behavioral_context(cand)
        d_expl = explain_drift_cross_check(cand)
        limits = build_candidate_limitations(cand)

        candidate_explanation = CandidateExplanation(
            rank=cand.rank,
            vessel_id=cand.vessel_id,
            candidate_id=cand.candidate_id,
            mmsi=cand.mmsi,
            imo=cand.imo,
            name=cand.name,
            vessel_type=cand.vessel_type,
            evidence_consistency_score=cand.evidence_consistency_score,
            evidence_consistency_level=consistency_level,
            evidence_availability_ratio=cand.evidence_availability_ratio,
            valid_primary_channels=cand.valid_primary_channels,
            total_primary_channels=cand.total_primary_channels,
            evidence_availability_summary=avail_summary,
            evidence_availability_band=avail_band,
            spatial_score=cand.spatial_score,
            spatial_explanation=sp_expl,
            temporal_score=cand.temporal_score,
            temporal_explanation=tp_expl,
            trajectory_score=cand.trajectory_score,
            trajectory_explanation=tr_expl,
            behavioral_context=b_expl,
            drift_cross_check=d_expl,
            limitations=limits,
            scientific_disclaimer=SCIENTIFIC_DISCLAIMER,
            provenance={
                "candidate_ranking_id": candidate_ranking.id,
                "f2_rank": cand.rank,
                "f2_score": cand.evidence_consistency_score,
                "spatial_discrepancy_km": sp_disp,
                "temporal_discrepancy_hours": tp_disp,
                "zero_fabrication": True,
            },
        )
        explained_candidates.append(candidate_explanation)

    report = ExplainabilityReport(
        id=res_id,
        investigation_id=target_inv,
        spill_id=target_spill,
        candidate_ranking_id=candidate_ranking.id,
        generated_at=now_utc,
        methodology_version="F3-1.0.0",
        candidate_count=len(explained_candidates),
        candidates=explained_candidates,
        scientific_disclaimer=SCIENTIFIC_DISCLAIMER,
        metadata={
            "stage": "F3",
            "asset_type": "explainability_report",
            "zero_fabrication": True,
            "methodology": "Stage F3 Explainability & Uncertainty",
            "scoring_rule": "F2 evidence consistency scores and ranks are authoritative and preserved without modification.",
            "source_zone_description": "Analytical search envelope, not a statistical confidence region.",
            "confidence_metric_policy": "Uncertainty communicated through evidence availability and explicit limitations only; no fabricated statistical values generated.",
        },
    )

    # Determine artifact directory
    if output_dir is not None:
        target_dir = Path(output_dir)
    else:
        safe_inv = _sanitize(target_inv, "investigation")
        safe_spill = _sanitize(target_spill, "spill")
        target_dir = (
            Path(settings.data_dir)
            / "derived"
            / safe_inv
            / "explainability"
            / safe_spill
        )

    target_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = target_dir / "explainability_report.json"
    artifact_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")

    # Register derived DOCUMENT asset
    artifact = AcquiredArtifact(
        asset_type=AssetType.DOCUMENT,
        location=str(artifact_path.resolve()),
        source="explainability_service",
        acquisition_time=now_utc,
        provenance=Provenance(
            product_id=res_id,
            retrieved_at=now_utc,
            processing_level="explainability_report",
            notes=(
                f"Stage F3 explainability & uncertainty report for {len(explained_candidates)} candidate vessels. "
                f"Spill={target_spill}, CandidateRanking={candidate_ranking.id}. Zero-fabrication guarantee."
            ),
            extra={
                "asset_type": "explainability_report",
                "stage": "F3",
                "spill_detection_id": target_spill,
                "candidate_ranking_id": candidate_ranking.id,
                "explained_candidate_count": len(explained_candidates),
                "zero_fabrication": True,
            },
        ),
        metadata={
            "asset_type": "explainability_report",
            "stage": "F3",
            "investigation_id": target_inv,
            "spill_id": target_spill,
            "candidate_ranking_id": candidate_ranking.id,
            "explained_candidate_count": len(explained_candidates),
            "zero_fabrication": True,
        },
    )

    derived_asset = target_registry.register(
        target_inv, "explainability_service", artifact
    )
    report.asset_id = derived_asset.id

    return report, derived_asset


# ---------------------------------------------------------------------------
# Loader Helper
# ---------------------------------------------------------------------------


def load_candidate_ranking_from_asset(
    asset: Asset,
    registry: AssetRegistry | None = None,
) -> CandidateRanking:
    """Load or reconstruct a CandidateRanking from an asset."""
    artifact_path = Path(asset.location)
    if not artifact_path.exists():
        raise ExplainabilityError(f"Candidate ranking artifact does not exist: {artifact_path}")

    try:
        content = artifact_path.read_text(encoding="utf-8")
        return CandidateRanking.model_validate_json(content)
    except Exception as exc:
        raise ExplainabilityError(f"Malformed candidate ranking artifact: {exc}") from exc
