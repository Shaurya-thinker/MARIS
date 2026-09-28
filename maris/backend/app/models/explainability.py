"""Domain models for MARIS Stage F3 — Explainability & Uncertainty.

Stage F3 consumes the Stage F2 CandidateRanking output and produces a deterministic,
investigator-readable ExplainabilityReport.

CORE ARCHITECTURAL INVARIANTS:
1. F2 IS AUTHORITATIVE:
   - Candidate ordering, rank, evidence_consistency_score, primary channel scores,
     and evidence availability are established authoritatively by Stage F2.
   - F3 only explains and qualifies those results; it MUST NOT recalculate or modify them.
   - F3 MUST preserve the exact candidate order supplied by F2.

2. EVIDENCE CONSISTENCY LEVEL:
   - Deterministic descriptive bands derived strictly from F2 evidence_consistency_score:
     score >= 0.75: HIGH
     score >= 0.50 and < 0.75: MODERATE
     score < 0.50: LOW
     score is None: INSUFFICIENT_DATA
   - These are descriptive bands only; NOT confidence, probability, or guilt.

3. UNCERTAINTY COMMUNICATION:
   - Uncertainty is communicated through evidence availability, explicit limitations,
     data coverage state, and model assumptions.
   - NO unsupported confidence percentages (e.g. "95% confidence"), NO unsupported
     confidence intervals, and NO probability distributions.

4. CONTEXTUAL INFORMATION:
   - E3 behavioral intelligence and D1 forward drift are explicitly labeled contextual
     and non-additive. They do NOT contribute to the score.

5. SCIENTIFIC DISCLAIMER:
   - Included in every report and candidate explanation to reinforce legal and scientific boundaries.

6. ZERO FABRICATION:
   - Zero synthetic AIS positions, zero fabricated uncertainty values.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.models.evidence_fusion import (
    BehavioralContextSummary,
    ForwardDriftCrossCheck,
)

SCIENTIFIC_DISCLAIMER: str = (
    "This analysis ranks vessels by consistency with the available satellite, "
    "environmental, and AIS evidence. It does not establish causation, legal responsibility, "
    "or culpability. Results are subject to data coverage, measurement uncertainty, "
    "model assumptions, and the limitations listed for each candidate."
)

LEGAL_DISCLAIMER_SHORT: str = (
    "This analysis is an evidence-consistency assessment and is not a legal determination "
    "of responsibility or causation."
)

FORBIDDEN_ATTRIBUTION_TERMS: list[str] = [
    "guilty",
    "culprit",
    "perpetrator",
    "definitely caused",
    "responsible vessel",
    "proven responsible",
    "confirmed cause",
    "probability of responsibility",
    "probability of guilt",
]


def verify_scientific_vocabulary(text: str) -> tuple[bool, list[str]]:
    """Verify that a text does not contain unsupported attribution or culpability terms.

    Returns:
        (is_valid, list_of_detected_violations)
    """
    lower = text.lower()
    violations: list[str] = []
    for term in FORBIDDEN_ATTRIBUTION_TERMS:
        if term in lower:
            violations.append(term)
    return len(violations) == 0, violations


class EvidenceConsistencyLevel(str, Enum):
    """Deterministic descriptive band for evidence consistency score."""

    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class DriftCrossCheckStatus(str, Enum):
    """Deterministic status classification for D1 forward drift cross-check."""

    CONSISTENT = "CONSISTENT"
    NOT_CONSISTENT = "NOT_CONSISTENT"
    UNAVAILABLE = "UNAVAILABLE"


class BehavioralExplanation(BaseModel):
    """Contextual behavioral intelligence explanation."""

    label: str = Field(default="Contextual behavioral observations")
    observations: list[str] = Field(default_factory=list)
    numerical_contribution_statement: str = Field(
        default="Contextual behavioral observations do not contribute numerically to the evidence_consistency_score."
    )
    raw_summary: BehavioralContextSummary = Field(default_factory=BehavioralContextSummary)


class DriftCrossCheckExplanation(BaseModel):
    """Contextual forward drift cross-check explanation."""

    label: str = Field(default="Forward drift cross-check")
    status: DriftCrossCheckStatus = Field(default=DriftCrossCheckStatus.UNAVAILABLE)
    min_distance_km: float | None = None
    explanation: str
    non_additive_statement: str = Field(
        default="Forward drift cross-check is non-additive contextual evidence and does not modify the consistency score."
    )
    raw_cross_check: ForwardDriftCrossCheck = Field(default_factory=ForwardDriftCrossCheck)


# ---------------------------------------------------------------------------
# Structured F3 Components (Investigator-facing & Frontend consumption)
# ---------------------------------------------------------------------------


class CandidateSummary(BaseModel):
    """Concise candidate identity and consistency summary."""

    rank: int = Field(ge=1, description="Stage F2 authoritative rank.")
    candidate_id: str = Field(description="Unique candidate identifier.")
    vessel_id: str = Field(description="Vessel identifier.")
    vessel_name: str | None = Field(default=None, description="Reported vessel name.")
    vessel_type: str | None = Field(default=None, description="Vessel classification.")
    mmsi: str | None = Field(default=None, description="Vessel MMSI.")
    imo: str | None = Field(default=None, description="Vessel IMO.")
    flag: str | None = Field(default=None, description="Vessel flag state if known.")
    evidence_consistency_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Dimensionless evidence consistency score [0, 1].",
    )
    evidence_consistency_level: EvidenceConsistencyLevel = Field(
        default=EvidenceConsistencyLevel.INSUFFICIENT_DATA,
        description="Descriptive band: HIGH / MODERATE / LOW / INSUFFICIENT_DATA.",
    )


class EvidenceBreakdown(BaseModel):
    """Detailed multi-channel evidence evaluation breakdown."""

    spatial_score: float | None = Field(default=None, ge=0.0, le=1.0)
    spatial_explanation: str
    temporal_score: float | None = Field(default=None, ge=0.0, le=1.0)
    temporal_explanation: str
    trajectory_score: float | None = Field(default=None, ge=0.0, le=1.0)
    trajectory_explanation: str
    source_zone_score: float | None = Field(default=None, ge=0.0, le=1.0)
    source_zone_explanation: str | None = None
    data_quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    data_quality_explanation: str | None = None

    active_weights: dict[str, float] = Field(
        default_factory=dict,
        description="Weights applied to available channels.",
    )
    active_weight_sum: float = Field(
        default=0.0,
        ge=0.0,
        description="Denominator sum of active weights.",
    )
    available_dimensions: list[str] = Field(
        default_factory=list,
        description="Evidence dimensions evaluated in consistency score.",
    )
    unavailable_dimensions: list[str] = Field(
        default_factory=list,
        description="Evidence dimensions excluded from consistency score.",
    )


class UncertaintyBreakdown(BaseModel):
    """Explicit multi-source uncertainty and model limitation breakdown."""

    source_uncertainty_km: float | None = Field(
        default=None,
        description="Stage D3 source candidate zone search radius in km.",
    )
    source_uncertainty_explanation: str = Field(
        description="Explanation of analytical search envelope and uncertainty radius.",
    )
    ais_sparsity: str | None = Field(
        default=None,
        description="AIS trajectory density descriptor (e.g. 'sparse', 'nominal').",
    )
    is_sparse_track: bool = Field(
        default=False,
        description="Flag indicating track has <= 2 genuine AIS observations.",
    )
    environmental_fallback: bool = Field(
        default=False,
        description="Flag indicating wind-only current fallback was applied.",
    )
    environmental_fallback_explanation: str | None = Field(
        default=None,
        description="Explanation of environmental surface-current fallback status.",
    )
    shoreline_termination: bool = Field(
        default=False,
        description="Flag indicating backward trajectory terminated at coast.",
    )
    shoreline_termination_explanation: str | None = Field(
        default=None,
        description="Explanation of coastal trajectory termination.",
    )
    interpolation_status: str | None = Field(
        default=None,
        description="Environmental grid resolution limitation status.",
    )
    raw_uncertainty: dict[str, Any] = Field(
        default_factory=dict,
        description="Full Stage F1/F2 uncertainty summary dictionary.",
    )


class ProvenanceBreakdown(BaseModel):
    """Structured data provenance distinguishing real vs curated benchmark data."""

    ais_source: str = Field(description="AIS data source origin.")
    satellite_source: str = Field(
        default="Sentinel-1 SAR",
        description="Satellite sensor source.",
    )
    environmental_source: str = Field(
        default="Copernicus Marine / ERA5",
        description="Meteo-oceanic environmental source.",
    )
    source_estimation_source: str = Field(
        default="Stage D3 backward-drift model",
        description="Upstream source estimation service.",
    )
    is_real_observation: bool = Field(
        default=False,
        description="True only if AIS represents verified live operational records.",
    )
    data_source_type: str = Field(
        default="curated_historical_reconstruction",
        description="Detailed provenance type code.",
    )
    provenance_summary: str = Field(
        description="Prominently stated distinction between real and reference/curated data.",
    )
    raw_provenance: dict[str, Any] = Field(
        default_factory=dict,
        description="Full Stage F1/F2 provenance dictionary.",
    )


class RankingContext(BaseModel):
    """Ranking context explaining candidate position and tie resolution."""

    rank: int = Field(ge=1)
    total_candidates: int = Field(ge=0)
    evidence_consistency_score: float | None = None
    evidence_availability_ratio: float = 0.0
    is_tied_score: bool = False
    tie_break_rule: str | None = None
    tie_break_resolution: str | None = None
    explanation: str = Field(
        description="Explanation of rank position and deterministic tie-breaking without attribution.",
    )


class StatementTraceability(BaseModel):
    """Audit traceability linking an explanation statement to its upstream stage and field."""

    statement_type: str = Field(description="Category of statement (e.g. spatial_proximity, temporal_offset).")
    source_stage: str = Field(description="Upstream processing stage (E2, D3, F1, F2).")
    source_field: str = Field(description="Exact data model field name providing the value.")
    value: Any = Field(description="Exact quantitative or boolean value.")
    statement: str = Field(description="Human-readable statement derived from value.")


class ScoreExplanation(BaseModel):
    """Mathematical explanation of how the Evidence Consistency Score was formed."""

    score: float | None = None
    formula: str = Field(
        default="S_candidate = sum(w_i * S_i) / sum(w_i) for active available dimensions",
        description="Dimensionless evidence consistency score formula.",
    )
    active_dimensions: list[str] = Field(default_factory=list)
    unavailable_dimensions: list[str] = Field(default_factory=list)
    active_weights: dict[str, float] = Field(default_factory=dict)
    active_weight_sum: float = 0.0
    explanation_text: str = Field(
        description="Investigator-readable calculation breakdown.",
    )


class CandidateExplanation(BaseModel):
    """F3 explanation and uncertainty qualification for a single ranked candidate vessel."""

    rank: int = Field(ge=1, description="Authoritative 1-based rank from Stage F2.")
    vessel_id: str = Field(description="E1 CandidateVessel.vessel_id.")
    candidate_id: str = Field(description="E1 CandidateVessel.candidate_id.")
    mmsi: str | None = Field(default=None, description="Vessel MMSI.")
    imo: str | None = Field(default=None, description="Vessel IMO (if known).")
    name: str | None = Field(default=None, description="Vessel name.")
    vessel_type: str | None = Field(default=None, description="Vessel type (if known).")

    # Authoritative scores and descriptive level from F2
    evidence_consistency_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Preserved F2 evidence_consistency_score. Dimensionless compatibility index, NOT a probability.",
    )
    evidence_consistency_level: EvidenceConsistencyLevel = Field(
        description="Descriptive band: HIGH (>=0.75), MODERATE (>=0.50), LOW (<0.50), INSUFFICIENT_DATA (None)."
    )

    # Evidence Availability
    evidence_availability_ratio: float = Field(
        ge=0.0,
        le=1.0,
        description="Authoritative F2 ratio: valid_primary_channels / total_primary_channels.",
    )
    valid_primary_channels: int = Field(ge=0, le=3)
    total_primary_channels: int = Field(default=3, ge=1)
    evidence_availability_summary: str = Field(
        description="Factual summary of available primary evidence channels.",
    )
    evidence_availability_band: str = Field(
        description="Deterministic availability level: HIGH / MODERATE / LIMITED / NO evidence availability.",
    )

    # Primary Channel Explanations
    spatial_score: float | None = Field(default=None, ge=0.0, le=1.0)
    spatial_explanation: str
    temporal_score: float | None = Field(default=None, ge=0.0, le=1.0)
    temporal_explanation: str
    trajectory_score: float | None = Field(default=None, ge=0.0, le=1.0)
    trajectory_explanation: str

    # Contextual Explanations (non-additive)
    behavioral_context: BehavioralExplanation
    drift_cross_check: DriftCrossCheckExplanation

    # Limitations & Uncertainty
    limitations: list[str] = Field(
        default_factory=list,
        description="Factual, deterministic limitations applicable to this candidate.",
    )
    scientific_disclaimer: str = Field(
        default=SCIENTIFIC_DISCLAIMER,
        description="Mandatory scientific disclaimer on legal and attribution boundaries.",
    )

    provenance: dict[str, Any] = Field(
        default_factory=dict,
        description="Preserved F2 provenance metadata.",
    )

    # Extended Structured Explanations for Stage F3
    candidate_summary: CandidateSummary | None = Field(
        default=None,
        description="Concise candidate identity summary.",
    )
    evidence_breakdown: EvidenceBreakdown | None = Field(
        default=None,
        description="Structured multi-channel evidence breakdown.",
    )
    uncertainty: UncertaintyBreakdown | None = Field(
        default=None,
        description="Structured uncertainty and model limitations.",
    )
    provenance_details: ProvenanceBreakdown | None = Field(
        default=None,
        description="Structured data provenance details.",
    )
    ranking_context: RankingContext | None = Field(
        default=None,
        description="Ranking placement and tie resolution context.",
    )
    score_explanation: ScoreExplanation | None = Field(
        default=None,
        description="Mathematical explanation of consistency score.",
    )
    narrative: str = Field(
        default="",
        description="Synthesized investigator-readable factual narrative.",
    )
    traceability: list[StatementTraceability] = Field(
        default_factory=list,
        description="Itemized evidence traceability for factual narrative claims.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Specific scientific or observational warnings for this candidate.",
    )

    @property
    def vessel_name(self) -> str | None:
        """Alias for name."""
        return self.name


class ExplainabilityRequest(BaseModel):
    """Request payload for Stage F3 explainability."""

    candidate_ranking_id: str | None = Field(
        default=None,
        description="ID of Stage F2 CandidateRanking or its registered asset. If omitted, auto-discovered.",
    )
    evidence_fusion_id: str | None = Field(
        default=None,
        description="Optional ID of Stage F1 EvidenceFusionResult or asset.",
    )


class ExplainabilityReport(BaseModel):
    """Stage F3 Explainability & Uncertainty report artifact."""

    id: str
    investigation_id: str
    spill_id: str
    candidate_ranking_id: str
    generated_at: datetime
    methodology_version: str = "F3-1.0.0"
    asset_id: str | None = Field(
        default=None,
        description="AssetType.DOCUMENT asset registered in AssetRegistry for this report.",
    )

    candidate_count: int = Field(ge=0, description="Total number of candidates explained.")
    candidates: list[CandidateExplanation] = Field(
        description="Candidates explained in exact Stage F2 ranking sequence.",
    )
    candidate_explanations: list[CandidateExplanation] = Field(
        default_factory=list,
        description="Alias list of candidate explanations for F3 interface compatibility.",
    )

    summary: str = Field(
        default="",
        description="High-level investigation and candidate evaluation summary.",
    )
    comparison_summary: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Comparative evaluations between ranked candidates.",
    )
    methodology: dict[str, Any] = Field(
        default_factory=dict,
        description="Authoritative scoring method, weights, and tie-break rules.",
    )
    data_quality_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Summary of overall data coverage, uncertainty, and benchmark status.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Overall investigation-level warnings.",
    )

    scientific_disclaimer: str = Field(
        default=SCIENTIFIC_DISCLAIMER,
        description="Mandatory scientific disclaimer.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

