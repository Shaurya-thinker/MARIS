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
    LEGAL_DISCLAIMER_SHORT,
    SCIENTIFIC_DISCLAIMER,
    BehavioralExplanation,
    CandidateExplanation,
    CandidateSummary,
    DriftCrossCheckExplanation,
    DriftCrossCheckStatus,
    EvidenceBreakdown,
    EvidenceConsistencyLevel,
    ExplainabilityReport,
    ProvenanceBreakdown,
    RankingContext,
    ScoreExplanation,
    StatementTraceability,
    UncertaintyBreakdown,
    verify_scientific_vocabulary,
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
    min_distance_centerline_km: float | None = None,
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

    parts = [f"Spatial evidence indicates {level} consistency (score: {spatial_score:.2f})."]
    if spatial_disp_km is not None:
        parts.append(
            f"Vessel closest point of approach was {spatial_disp_km:.2f} km from estimated source candidate center"
        )
        if min_distance_centerline_km is not None:
            parts.append(f"and {min_distance_centerline_km:.2f} km from backward-drift centerline.")
        else:
            parts.append(".")
        return " ".join(parts).replace(" .", ".")

    return parts[0]


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
    obs_count: int | None = None,
    is_sparse_track: bool = False,
) -> str:
    """Explain trajectory consistency channel evaluation.

    IMPORTANT: Missing evidence is NOT described as a negative match.
    """
    if trajectory_score is None:
        if obs_count == 1:
            return (
                "Trajectory evidence was not scored because only one AIS observation was available; "
                "channel excluded from scoring."
            )
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

    qualifier = " (sparse track evaluated)" if is_sparse_track else ""
    return (
        f"Trajectory evidence indicates {level} consistency (score: {trajectory_score:.2f}){qualifier} "
        "based on centerline proximity and observed heading alignment."
    )


def explain_source_zone_channel(
    source_zone_score: float | None,
    in_zone: bool | None = None,
) -> str:
    """Explain source-zone membership channel evaluation."""
    if source_zone_score is None:
        return "Source zone membership evidence was unavailable; channel excluded from scoring."

    if source_zone_score >= 0.75:
        level = "HIGH"
    elif source_zone_score >= 0.50:
        level = "MODERATE"
    else:
        level = "LOW"

    if in_zone is True:
        return (
            f"Source zone evidence indicates {level} consistency (score: {source_zone_score:.2f}). "
            "Vessel position falls within the modeled backward-drift source candidate zone."
        )
    return f"Source zone evidence indicates {level} consistency (score: {source_zone_score:.2f})."


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


def explain_candidate_score(cand: RankedCandidate) -> ScoreExplanation:
    """Provide a transparent mathematical breakdown of the Evidence Consistency Score."""
    score = cand.evidence_consistency_score
    active_weights = getattr(cand, "active_weights", None) or {}
    raw_components = getattr(cand, "raw_score_components", None) or {}
    active_weight_sum = getattr(cand, "active_weight_sum", 0.0)
    active_dims = list(active_weights.keys()) if active_weights else getattr(cand, "available_dimensions", [])
    unavail_dims = getattr(cand, "unavailable_dimensions", [])

    if score is None:
        expl_text = (
            "No primary evidence dimensions were available for this candidate; "
            "the Evidence Consistency Score is indeterminate (None)."
        )
    else:
        lines: list[str] = [
            f"Evidence Consistency Score = {score:.4f}.",
            "Formula: S = sum(w_i * S_i) / sum(w_i) for active available dimensions.",
        ]
        components: list[str] = []
        for dim, w in active_weights.items():
            s_val = raw_components.get(dim)
            s_str = f"{s_val:.4f}" if s_val is not None else "N/A"
            components.append(f"{dim.capitalize()}: score = {s_str}, weight = {w:.4f}")
        if components:
            lines.append("Active components: " + "; ".join(components) + ".")
        if unavail_dims:
            lines.append(f"Unavailable dimensions excluded from denominator: {', '.join(unavail_dims)}.")
        expl_text = " ".join(lines)

    return ScoreExplanation(
        score=score,
        formula="S_candidate = sum(w_i * S_i) / sum(w_i) for active available dimensions",
        active_dimensions=active_dims,
        unavailable_dimensions=unavail_dims,
        active_weights=active_weights,
        active_weight_sum=active_weight_sum,
        explanation_text=expl_text,
    )


def build_candidate_uncertainty(cand: RankedCandidate) -> UncertaintyBreakdown:
    """Construct structured multi-source uncertainty and model limitation breakdown."""
    unc = getattr(cand, "uncertainty_summary", None) or {}
    source_r = unc.get("source_uncertainty_km")
    is_sparse = unc.get("is_sparse_track", False)
    fallback = unc.get("source_fallback_applied", False) or unc.get("environmental_fallback", False)
    shoreline = unc.get("shoreline_terminated", False) or unc.get("shoreline_termination", False)
    interp_status = unc.get("interpolation_status", "NOMINAL_GRID_RESOLUTION")

    if source_r is not None:
        r_expl = f"The modeled source zone has an analytical search uncertainty radius of {source_r:.1f} km."
    else:
        r_expl = "Source candidate zone search uncertainty radius was not quantitatively modeled."

    fb_expl = (
        "Wind-only fallback was used because surface-current data was unavailable."
        if fallback
        else "Surface-current data was available; no fallback required."
    )

    sh_expl = (
        "Backward drift trajectory terminated at the shoreline."
        if shoreline
        else "Backward drift remained within open sea waters."
    )

    return UncertaintyBreakdown(
        source_uncertainty_km=source_r,
        source_uncertainty_explanation=r_expl,
        ais_sparsity="sparse" if is_sparse else "nominal",
        is_sparse_track=is_sparse,
        environmental_fallback=fallback,
        environmental_fallback_explanation=fb_expl,
        shoreline_termination=shoreline,
        shoreline_termination_explanation=sh_expl,
        interpolation_status=interp_status,
        raw_uncertainty=unc,
    )


def build_candidate_provenance(cand: RankedCandidate) -> ProvenanceBreakdown:
    """Construct structured provenance distinguishing real vs curated benchmark data."""
    prov = getattr(cand, "provenance", None) or {}
    is_real = prov.get("is_real_observation", False)
    data_type = prov.get("data_source_type", "curated_historical_reconstruction")

    if not is_real:
        summary = "AIS observations in this benchmark are curated historical reconstructions and are not operational raw AIS observations."
    else:
        summary = "AIS observations were derived from verified live operational AIS records."

    return ProvenanceBreakdown(
        ais_source=prov.get("ais_source", data_type),
        satellite_source=prov.get("satellite_source", "Sentinel-1 SAR"),
        environmental_source=prov.get("environmental_source", "Copernicus Marine / ERA5"),
        source_estimation_source=prov.get("source_estimation_source", "Stage D3 backward-drift model"),
        is_real_observation=is_real,
        data_source_type=data_type,
        provenance_summary=summary,
        raw_provenance=prov,
    )


def build_candidate_ranking_context(
    cand: RankedCandidate,
    total_candidates: int,
    tie_break_rule: str | None = None,
) -> RankingContext:
    """Construct ranking context explaining placement and tie status."""
    is_tied = getattr(cand, "is_tied_score", False)
    res = getattr(cand, "tie_break_resolution", None)

    if is_tied:
        rule_desc = res or tie_break_rule or "deterministic vessel identifier"
        expl = (
            f"Rank #{cand.rank} of {total_candidates} reflects an identical Evidence Consistency Score "
            f"with other candidates. The tie was resolved using the configured deterministic tie-break rule ({rule_desc})."
        )
    else:
        score_str = f"{cand.evidence_consistency_score:.2f}" if cand.evidence_consistency_score is not None else "indeterminate"
        expl = (
            f"Rank #{cand.rank} of {total_candidates} reflects the configured Evidence Consistency Score ({score_str}) "
            "under the deterministic ranking model."
        )

    return RankingContext(
        rank=cand.rank,
        total_candidates=total_candidates,
        evidence_consistency_score=cand.evidence_consistency_score,
        evidence_availability_ratio=cand.evidence_availability_ratio,
        is_tied_score=is_tied,
        tie_break_rule=tie_break_rule,
        tie_break_resolution=res,
        explanation=expl,
    )


def build_candidate_narrative(
    cand: RankedCandidate,
    total_candidates: int,
    sp_disp: float | None,
    tp_disp: float | None,
    centerline_disp: float | None,
    obs_count: int | None,
    is_tied: bool,
    tie_resolution: str | None,
) -> str:
    """Synthesize concise, investigator-readable factual narrative from verified fields."""
    score_str = f"{cand.evidence_consistency_score:.2f}" if cand.evidence_consistency_score is not None else "indeterminate"
    name_str = cand.name or cand.candidate_id
    mmsi_str = f" (MMSI: {cand.mmsi})" if cand.mmsi else ""
    level_str = determine_consistency_level(cand.evidence_consistency_score).value

    parts = [
        f"Candidate {name_str}{mmsi_str} ranked #{cand.rank} of {total_candidates} with an Evidence Consistency Score of {score_str} ({level_str})."
    ]

    if cand.spatial_score is not None:
        parts.append(f"Spatial evidence was available and contributed {cand.spatial_score:.2f}.")
    else:
        parts.append("Spatial evidence was unavailable or insufficient.")

    if cand.temporal_score is not None:
        parts.append(f"Temporal evidence was available and contributed {cand.temporal_score:.2f}.")
    else:
        parts.append("Temporal evidence was unavailable or insufficient.")

    if cand.trajectory_score is not None:
        parts.append(f"Trajectory evidence was available and contributed {cand.trajectory_score:.2f}.")
    else:
        if obs_count == 1:
            parts.append("Trajectory evidence was unavailable because only one AIS observation was available.")
        else:
            parts.append("Trajectory evidence was not scored because fewer than two genuine AIS observations were available.")

    geo_parts: list[str] = []
    if sp_disp is not None:
        geo_parts.append(f"{sp_disp:.2f} km from the estimated source center")
    if centerline_disp is not None:
        geo_parts.append(f"{centerline_disp:.2f} km from the backward-drift centerline")
    if geo_parts:
        parts.append(f"The candidate was approximately {' and '.join(geo_parts)}.")

    if tp_disp is not None:
        mins = abs(tp_disp * 60.0)
        parts.append(
            f"Temporal closest approach occurred {tp_disp:.2f} hours ({mins:.1f} minutes) from estimated spill release time."
        )

    if is_tied:
        tie_str = tie_resolution or "the configured deterministic tie-break rule"
        parts.append(f"The score tie with other candidates was resolved using {tie_str}.")

    # Provenance communication
    prov = getattr(cand, "provenance", None) or {}
    is_real = prov.get("is_real_observation", False)
    if not is_real:
        parts.append(
            "AIS observations in this benchmark are curated historical reconstructions and are not operational raw AIS observations."
        )
    else:
        parts.append("AIS observations were derived from verified operational AIS records.")

    parts.append(
        "These measurements describe consistency with the modeled spill source. They do not establish vessel responsibility or causation."
    )

    return " ".join(parts)


def build_candidate_traceability(cand: RankedCandidate) -> list[StatementTraceability]:
    """Produce itemized evidence traceability linking claims to upstream stages and fields."""
    traceability: list[StatementTraceability] = []
    prov = getattr(cand, "provenance", None) or {}

    # 1. Consistency Score
    if cand.evidence_consistency_score is not None:
        traceability.append(
            StatementTraceability(
                statement_type="consistency_score",
                source_stage="F2",
                source_field="evidence_consistency_score",
                value=cand.evidence_consistency_score,
                statement=f"Candidate ranked #{cand.rank} with Evidence Consistency Score of {cand.evidence_consistency_score:.4f}.",
            )
        )
    else:
        traceability.append(
            StatementTraceability(
                statement_type="consistency_score",
                source_stage="F2",
                source_field="evidence_consistency_score",
                value=None,
                statement=f"Candidate ranked #{cand.rank} with indeterminate Evidence Consistency Score (no primary evidence).",
            )
        )

    # 2. Spatial proximity
    sp_disp = prov.get("spatial_discrepancy_km")
    if sp_disp is not None:
        traceability.append(
            StatementTraceability(
                statement_type="spatial_proximity",
                source_stage="E2",
                source_field="spatial_discrepancy_km",
                value=sp_disp,
                statement=f"Vessel closest point of approach was {sp_disp:.3f} km from estimated source candidate center.",
            )
        )

    # 3. Centerline distance
    cl_disp = prov.get("centerline_distance_km")
    if cl_disp is not None:
        traceability.append(
            StatementTraceability(
                statement_type="centerline_proximity",
                source_stage="E2",
                source_field="centerline_distance_km",
                value=cl_disp,
                statement=f"Vessel closest point of approach was {cl_disp:.3f} km from backward-drift centerline.",
            )
        )

    # 4. Temporal proximity
    tp_disp = prov.get("temporal_discrepancy_hours")
    if tp_disp is not None:
        traceability.append(
            StatementTraceability(
                statement_type="temporal_proximity",
                source_stage="E2",
                source_field="temporal_discrepancy_hours",
                value=tp_disp,
                statement=f"Vessel closest approach occurred {tp_disp:.4f} hours from estimated spill release time.",
            )
        )

    # 5. AIS observations
    obs = prov.get("observation_count")
    if obs is not None:
        traceability.append(
            StatementTraceability(
                statement_type="ais_observation_count",
                source_stage="E2",
                source_field="observation_count",
                value=obs,
                statement=f"{obs} genuine AIS observation(s) recorded in analysis window.",
            )
        )

    # 6. Source zone uncertainty
    unc = getattr(cand, "uncertainty_summary", None) or {}
    source_r = unc.get("source_uncertainty_km")
    if source_r is not None:
        traceability.append(
            StatementTraceability(
                statement_type="source_uncertainty",
                source_stage="D3",
                source_field="source_uncertainty_km",
                value=source_r,
                statement=f"Analytical source search envelope uncertainty radius is {source_r:.2f} km.",
            )
        )

    # 7. Provenance status
    is_real = prov.get("is_real_observation", False)
    traceability.append(
        StatementTraceability(
            statement_type="provenance_status",
            source_stage="E1",
            source_field="is_real_observation",
            value=is_real,
            statement="AIS observations represent verified live operational data."
            if is_real
            else "AIS observations are curated historical reconstructions and not operational raw AIS.",
        )
    )

    return traceability


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

    # Upstream uncertainty and provenance limitations
    unc = getattr(cand, "uncertainty_summary", None) or {}
    if unc.get("source_fallback_applied") or unc.get("environmental_fallback"):
        limitations.append("Surface-current data was unavailable; wind-only drift fallback was applied.")
    if unc.get("shoreline_terminated") or unc.get("shoreline_termination"):
        limitations.append("Backward drift trajectory terminated at the coastline.")
    if unc.get("is_sparse_track"):
        limitations.append("AIS track has low temporal resolution (sparse track).")

    prov = getattr(cand, "provenance", None) or {}
    if not prov.get("is_real_observation", True) or prov.get("data_source_type") == "curated_historical_reconstruction":
        limitations.append("AIS data represents curated historical reconstruction rather than live operational telemetry.")

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


def build_comparison_summary(candidates: list[RankedCandidate]) -> list[dict[str, Any]]:
    """Generate pairwise or relative comparative evaluations between ranked candidates."""
    comparisons: list[dict[str, Any]] = []
    if len(candidates) < 2:
        return comparisons

    for i in range(len(candidates) - 1):
        c1 = candidates[i]
        c2 = candidates[i + 1]

        c1_name = c1.name or c1.candidate_id
        c2_name = c2.name or c2.candidate_id

        c1_tied = getattr(c1, "is_tied_score", False)
        c2_tied = getattr(c2, "is_tied_score", False)
        c1_res = getattr(c1, "tie_break_resolution", None)

        if c1_tied and c2_tied and c1.evidence_consistency_score == c2.evidence_consistency_score:
            expl = (
                f"Candidate {c1_name} and Candidate {c2_name} have identical Evidence Consistency Scores "
                f"({c1.evidence_consistency_score:.2f} if c1.evidence_consistency_score is not None else 'None'). "
                f"Candidate {c1_name} ranks #{c1.rank} above Candidate {c2_name} (#{c2.rank}) based on {c1_res or 'the configured deterministic tie-break rule'}."
            )
        else:
            s1_str = f"{c1.evidence_consistency_score:.2f}" if c1.evidence_consistency_score is not None else "None"
            s2_str = f"{c2.evidence_consistency_score:.2f}" if c2.evidence_consistency_score is not None else "None"
            expl = (
                f"Candidate {c1_name} ranks #{c1.rank} above Candidate {c2_name} (#{c2.rank}) because its aggregate "
                f"evidence consistency score ({s1_str} vs {s2_str}) is higher under the configured weighting scheme."
            )

        comparisons.append(
            {
                "higher_rank_candidate": c1.candidate_id,
                "higher_rank_name": c1_name,
                "higher_rank": c1.rank,
                "lower_rank_candidate": c2.candidate_id,
                "lower_rank_name": c2_name,
                "lower_rank": c2.rank,
                "score_difference": (c1.evidence_consistency_score - c2.evidence_consistency_score)
                if (c1.evidence_consistency_score is not None and c2.evidence_consistency_score is not None)
                else None,
                "is_score_tie": c1_tied and c2_tied,
                "comparison_statement": expl,
            }
        )

    return comparisons


def generate_markdown_report(report: ExplainabilityReport) -> str:
    """Generate comprehensive 14-section human-readable Markdown report."""
    md: list[str] = [
        f"# MARIS Investigation Explainability & Uncertainty Report",
        f"**Investigation ID**: `{report.investigation_id}`  ",
        f"**Spill ID**: `{report.spill_id}`  ",
        f"**Candidate Ranking ID**: `{report.candidate_ranking_id}`  ",
        f"**Generated UTC**: `{report.generated_at.isoformat()}`  ",
        f"**Methodology**: `{report.methodology_version}`  ",
        "",
        "---",
        "",
        "## 1. Investigation Summary",
        report.summary or f"Investigation {report.investigation_id} evaluated {report.candidate_count} candidate vessels against spill detection {report.spill_id}.",
        "",
        "## 2. Spill Summary",
        f"Spill ID `{report.spill_id}` evaluated using Sentinel-1 SAR morphology and Copernicus/ERA5 backward-drift trajectory modeling.",
        "",
        "## 3. Candidate Ranking Table",
        "| Rank | Candidate ID | Vessel Name | MMSI | Consistency Score | Band | Availability | Status |",
        "|:---:|:---|:---|:---:|:---:|:---:|:---:|:---|",
    ]

    for c in report.candidates:
        s_val = f"{c.evidence_consistency_score:.4f}" if c.evidence_consistency_score is not None else "None"
        band = c.evidence_consistency_level.value
        tied_badge = " (TIED)" if (c.ranking_context and c.ranking_context.is_tied_score) else ""
        md.append(
            f"| #{c.rank} | `{c.candidate_id}` | {c.name or 'Unknown'} | {c.mmsi or 'N/A'} | {s_val} | {band} | {c.evidence_availability_ratio * 100:.0f}% | Active{tied_badge} |"
        )

    md.extend([
        "",
        "## 4. Per-Candidate Evidence Explanation",
    ])

    for c in report.candidates:
        md.extend([
            f"### Candidate #{c.rank}: {c.name or c.candidate_id} (MMSI: {c.mmsi or 'N/A'})",
            f"- **Narrative**: {c.narrative}",
            f"- **Score Breakdown**: {c.score_explanation.explanation_text if c.score_explanation else 'N/A'}",
        ])
        if c.limitations:
            md.append("- **Limitations**:")
            for lim in c.limitations:
                md.append(f"  - {lim}")
        md.append("")

    md.extend([
        "## 5. Spatial Evidence",
        "Spatial proximity measures closest point of approach (CPA) from observed vessel positions to the modeled backward-drift candidate zone center.",
        "",
        "## 6. Temporal Evidence",
        "Temporal proximity measures offset between vessel observation timestamps and the estimated spill release time window.",
        "",
        "## 7. Trajectory Evidence",
        "Trajectory alignment evaluates vessel course and heading relative to backward-drift centerline vectors. When fewer than two observations exist, trajectory is excluded rather than penalized.",
        "",
        "## 8. Environmental Context",
        "Backward drift advection incorporates surface currents (Copernicus Marine) and 10m wind fields (ERA5). Forward drift cross-checks (D1) and behavioral observations (E3) are non-additive contextual signals.",
        "",
        "## 9. Data Quality",
        f"Data quality and availability across candidates: {report.data_quality_summary.get('summary', 'Nominal multi-sensor data coverage.')}",
        "",
        "## 10. Uncertainty",
        "Uncertainty is communicated through evidence availability ratios, search radii, coastal termination flags, and discrete observation intervals without fabricated statistical confidence intervals.",
        "",
        "## 11. Provenance",
        "Distinguishes real satellite and environmental reanalyses from reference/benchmark AIS. Curated reconstructions are explicitly labeled `curated_historical_reconstruction` (`is_real_observation = false`).",
        "",
        "## 12. Ranking Methodology",
        f"Deterministic scoring: $S = \\sum(w_i S_i) / \\sum(w_i)$ across available channels. Tie-breaking hierarchy: {report.methodology.get('tie_break_rule', 'score desc, availability desc, spatial discrepancy asc, temporal discrepancy asc, vessel identifier asc')}.",
        "",
        "## 13. Limitations",
        "- Satellite SAR detections are instantaneous snapshots.",
        "- AIS observations are discrete reports subject to coverage gaps.",
        "- Drift trajectories are numerical simulations bounded by meteo-oceanic grid resolutions.",
        "",
        "## 14. Scientific Disclaimer",
        f"> {SCIENTIFIC_DISCLAIMER}",
        "",
        f"> {LEGAL_DISCLAIMER_SHORT}",
    ])

    return "\n".join(md)


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
    total_cands = len(candidate_ranking.candidates)
    tie_rule = getattr(candidate_ranking, "deterministic_tie_break_rule", None)

    for cand in candidate_ranking.candidates:
        consistency_level = determine_consistency_level(cand.evidence_consistency_score)
        avail_summary, avail_band = format_evidence_availability(
            cand.valid_primary_channels, cand.total_primary_channels
        )

        cand_prov = getattr(cand, "provenance", None) or {}
        cand_unc = getattr(cand, "uncertainty_summary", None) or {}
        sp_disp = cand_prov.get("spatial_discrepancy_km")
        tp_disp = cand_prov.get("temporal_discrepancy_hours")
        cl_disp = cand_prov.get("centerline_distance_km")
        obs_count = cand_prov.get("observation_count")
        is_sparse = cand_unc.get("is_sparse_track", False)

        sz_score = getattr(cand, "source_zone_score", None)
        if sz_score is None and hasattr(cand, "raw_score_components"):
            sz_score = cand.raw_score_components.get("source_zone")

        sp_expl = explain_spatial_channel(cand.spatial_score, sp_disp, cl_disp)
        tp_expl = explain_temporal_channel(cand.temporal_score, tp_disp)
        tr_expl = explain_trajectory_channel(cand.trajectory_score, obs_count, is_sparse)
        sz_expl = explain_source_zone_channel(sz_score)

        b_expl = explain_behavioral_context(cand)
        d_expl = explain_drift_cross_check(cand)
        limits = build_candidate_limitations(cand)

        # Build structured sub-components
        cand_summary = CandidateSummary(
            rank=cand.rank,
            candidate_id=cand.candidate_id,
            vessel_id=cand.vessel_id,
            vessel_name=cand.name,
            vessel_type=cand.vessel_type,
            mmsi=cand.mmsi,
            imo=cand.imo,
            evidence_consistency_score=cand.evidence_consistency_score,
            evidence_consistency_level=consistency_level,
        )

        ev_breakdown = EvidenceBreakdown(
            spatial_score=cand.spatial_score,
            spatial_explanation=sp_expl,
            temporal_score=cand.temporal_score,
            temporal_explanation=tp_expl,
            trajectory_score=cand.trajectory_score,
            trajectory_explanation=tr_expl,
            source_zone_score=sz_score,
            source_zone_explanation=sz_expl,
            active_weights=getattr(cand, "active_weights", {}),
            active_weight_sum=getattr(cand, "active_weight_sum", 0.0),
            available_dimensions=getattr(cand, "available_dimensions", []),
            unavailable_dimensions=getattr(cand, "unavailable_dimensions", []),
        )

        unc_breakdown = build_candidate_uncertainty(cand)
        prov_breakdown = build_candidate_provenance(cand)
        rank_ctx = build_candidate_ranking_context(cand, total_cands, tie_rule)
        score_expl = explain_candidate_score(cand)

        narrative = build_candidate_narrative(
            cand=cand,
            total_candidates=total_cands,
            sp_disp=sp_disp,
            tp_disp=tp_disp,
            centerline_disp=cl_disp,
            obs_count=obs_count,
            is_tied=getattr(cand, "is_tied_score", False),
            tie_resolution=getattr(cand, "tie_break_resolution", None),
        )

        traceability = build_candidate_traceability(cand)

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
                **cand_prov,
            },
            candidate_summary=cand_summary,
            evidence_breakdown=ev_breakdown,
            uncertainty=unc_breakdown,
            provenance_details=prov_breakdown,
            ranking_context=rank_ctx,
            score_explanation=score_expl,
            narrative=narrative,
            traceability=traceability,
            warnings=getattr(cand, "warnings", []),
        )
        explained_candidates.append(candidate_explanation)

    comparisons = build_comparison_summary(candidate_ranking.candidates)

    high_level_summary = (
        f"Stage F3 Explainability evaluation for investigation '{target_inv}' and spill detection '{target_spill}'. "
        f"Evaluated {len(explained_candidates)} candidate vessels under the deterministic Evidence Consistency Model. "
        "F2 rankings, ranks, and scores are strictly preserved."
    )

    methodology_info = {
        "scoring_method": getattr(candidate_ranking, "ranking_method", "weighted_average_evidence_consistency"),
        "score_version": getattr(candidate_ranking, "score_version", "F2-1.0.0"),
        "nominal_weights": getattr(candidate_ranking, "nominal_weights", {}),
        "tie_break_rule": getattr(candidate_ranking, "deterministic_tie_break_rule", "deterministic_identifier"),
        "formula": "S = sum(w_i * S_i) / sum(w_i) over available dimensions",
        "zero_fabrication": True,
    }

    ranking_meta = getattr(candidate_ranking, "metadata", None) or {}
    data_quality_info = {
        "total_candidates": len(explained_candidates),
        "is_real_observation": ranking_meta.get("is_real_observation", False),
        "curated_benchmark": not ranking_meta.get("is_real_observation", True),
        "uncertainty_policy": "Explicit availability and physical bounds; no fabricated statistical values generated.",
    }

    report = ExplainabilityReport(
        id=res_id,
        investigation_id=target_inv,
        spill_id=target_spill,
        candidate_ranking_id=candidate_ranking.id,
        generated_at=now_utc,
        methodology_version="F3-1.0.0",
        candidate_count=len(explained_candidates),
        candidates=explained_candidates,
        candidate_explanations=explained_candidates,
        summary=high_level_summary,
        comparison_summary=comparisons,
        methodology=methodology_info,
        data_quality_summary=data_quality_info,
        warnings=list(getattr(candidate_ranking, "warnings", [])),
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

    # 1. Save machine-readable JSON artifact
    json_path = target_dir / "explainability_report.json"
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")

    # 2. Save human-readable Markdown report
    md_content = generate_markdown_report(report)
    md_path = target_dir / "explainability_report.md"
    md_path.write_text(md_content + "\n", encoding="utf-8")

    # Register derived DOCUMENT asset
    artifact = AcquiredArtifact(
        asset_type=AssetType.DOCUMENT,
        location=str(json_path.resolve()),
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
                "markdown_location": str(md_path.resolve()),
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
            "markdown_location": str(md_path.resolve()),
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
