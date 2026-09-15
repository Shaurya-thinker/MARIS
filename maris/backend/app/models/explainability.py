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
        description="Factual summary of available primary evidence channels (e.g. 'All three primary evidence channels were available.')."
    )
    evidence_availability_band: str = Field(
        description="Deterministic availability level: HIGH / MODERATE / LIMITED / NO evidence availability."
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

    scientific_disclaimer: str = Field(
        default=SCIENTIFIC_DISCLAIMER,
        description="Mandatory scientific disclaimer.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
