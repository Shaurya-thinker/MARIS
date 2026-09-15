"""Domain models for MARIS Stage F2 — Candidate Scoring & Ranking.

Stage F2 consumes the Stage F1 Evidence Fusion output and produces a deterministic,
comparative ranking of candidate vessels based on physical evidence consistency.

ARCHITECTURAL INVARIANTS:
1. PRIMARY SCORE ONLY:
   - Spatial proximity       (w = 0.50 nominal)
   - Temporal proximity      (w = 0.25 nominal)
   - Trajectory consistency  (w = 0.25 nominal)
   Score = weighted average of VALID primary channels.
   Unavailable/insufficient channels are excluded from the denominator.
   Never treat missing evidence as zero.
   Score remains in [0.0, 1.0].

2. BEHAVIORAL INTELLIGENCE (E3):
   - Contributes exactly 0.00 to the evidence consistency score.
   - Preserved as contextual evidence only.
   - More anomalies NEVER increase or decrease the score.

3. FORWARD DRIFT (D1):
   - Optional auxiliary cross-check only.
   - Contributes exactly 0.00 to the score.

4. COMPARATIVE RANKING:
   - Candidates are sorted by descending evidence_consistency_score.
   - Deterministic tie-breaking:
     1. Higher evidence_availability_ratio
     2. Smaller spatial discrepancy (km)
     3. Smaller temporal discrepancy (hours)
     4. Stable vessel identifier (lexicographical)

5. SCIENTIFIC LANGUAGE:
   - "evidence_consistency_score"
   - NOT probability of responsibility, guilt, causation, or legal evidence.
   - Ranking indicates comparative consistency with observed physical evidence.

6. ZERO FABRICATION:
   - No synthetic AIS positions, no track interpolation, no intent inference.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.evidence_fusion import (
    BehavioralContextSummary,
    ForwardDriftCrossCheck,
)


class RankedCandidate(BaseModel):
    """A candidate vessel ranked by comparative consistency with available physical evidence.

    SCIENTIFIC BOUNDARIES:
    - evidence_consistency_score is a physical compatibility index in [0.0, 1.0].
    - It is NOT a probability of responsibility, guilt, or legal liability.
    - E3 behavioral anomalies and D1 forward drift contribute exactly 0.00 to the score.
    """

    rank: int = Field(ge=1, description="1-based rank (1 = most consistent with physical evidence).")
    vessel_id: str = Field(description="E1 CandidateVessel.vessel_id.")
    candidate_id: str = Field(description="E1 CandidateVessel.candidate_id.")
    mmsi: str | None = Field(default=None, description="Vessel MMSI.")
    imo: str | None = Field(default=None, description="Vessel IMO (if known).")
    name: str | None = Field(default=None, description="Vessel name.")
    vessel_type: str | None = Field(default=None, description="Vessel type (if known).")

    # Primary Consistency Evaluation
    evidence_consistency_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Weighted average of valid primary channels in [0.0, 1.0]. "
            "None if no primary channels could be evaluated. "
            "Dimensionless physical compatibility index, NOT a probability."
        ),
    )
    spatial_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Normalised spatial proximity channel score in [0.0, 1.0]. None if unavailable.",
    )
    temporal_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Normalised temporal proximity channel score in [0.0, 1.0]. None if unavailable.",
    )
    trajectory_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Normalised trajectory consistency channel score in [0.0, 1.0]. None if unavailable.",
    )

    valid_primary_channels: int = Field(
        ge=0,
        le=3,
        description="Number of primary channels evaluated with valid genuine data.",
    )
    total_primary_channels: int = Field(
        default=3,
        ge=1,
        description="Total primary channels defined by the MARIS framework (= 3).",
    )
    evidence_availability_ratio: float = Field(
        ge=0.0,
        le=1.0,
        description="valid_primary_channels / total_primary_channels.",
    )

    # Contextual and cross-check signals (strictly 0.00 numerical contribution)
    contextual_evidence: BehavioralContextSummary = Field(
        default_factory=BehavioralContextSummary,
        description="E3 behavioral intelligence context. Contributes exactly 0.00 to the score.",
    )
    drift_cross_check: ForwardDriftCrossCheck = Field(
        default_factory=ForwardDriftCrossCheck,
        description="D1 forward drift cross-check. Contributes exactly 0.00 to the score.",
    )

    limitations: list[str] = Field(
        default_factory=list,
        description="Scientific, data-availability, and observational limitations.",
    )
    provenance: dict[str, Any] = Field(
        default_factory=dict,
        description="Detailed execution metadata, weights applied, and channel metric origins.",
    )

    @property
    def vessel_name(self) -> str | None:
        """Alias for name to ensure seamless compatibility."""
        return self.name


class CandidateRankingRequest(BaseModel):
    """Request payload for Stage F2 candidate ranking."""

    evidence_fusion_id: str | None = Field(
        default=None,
        description="ID of the Stage F1 EvidenceFusionResult or its registered asset. If omitted, auto-discovered.",
    )


class CandidateRanking(BaseModel):
    """Stage F2 Candidate Scoring & Ranking result artifact."""

    id: str
    investigation_id: str
    spill_id: str
    evidence_fusion_id: str
    generated_at: datetime
    methodology_version: str = "F2-1.0.0"
    asset_id: str | None = Field(
        default=None,
        description="AssetType.DOCUMENT asset registered in AssetRegistry for this ranking.",
    )

    # Primary scoring nominal weights
    nominal_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "spatial": 0.50,
            "temporal": 0.25,
            "trajectory": 0.25,
        }
    )

    candidate_count: int = Field(
        ge=0,
        description="Total number of candidates ranked.",
    )
    candidates: list[RankedCandidate] = Field(
        description="Candidate vessels sorted by descending evidence consistency score with deterministic tie-breaking.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def ranked_candidates(self) -> list[RankedCandidate]:
        """Alias for candidates to ensure seamless compatibility."""
        return self.candidates
