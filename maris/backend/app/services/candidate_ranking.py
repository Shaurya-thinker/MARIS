"""MARIS Stage F2 — Candidate Scoring & Ranking Service.

Stage F2 consumes the Stage F1 Evidence Fusion output and produces a deterministic,
comparative ranking of candidate vessels based on physical evidence consistency.

MANDATORY INVARIANTS:
1. PRIMARY SCORE ONLY:
   - Spatial proximity       (w = 0.50 nominal)
   - Temporal proximity      (w = 0.25 nominal)
   - Trajectory consistency  (w = 0.25 nominal)
   Score = weighted average of VALID primary channels.
   Unavailable or insufficient channels are excluded from the denominator.
   Never treat missing evidence as zero.
   Score remains in [0.0, 1.0].

2. BEHAVIORAL INTELLIGENCE (E3):
   - Contributes exactly 0.00 to the evidence consistency score.
   - Preserved as contextual evidence only.
   - More anomalies NEVER increase or decrease the score.

3. FORWARD DRIFT (D1):
   - Optional auxiliary cross-check only.
   - Contributes exactly 0.00 to the score.

4. COMPARATIVE RANKING & DETERMINISTIC TIE-BREAKING:
   - Candidates are sorted by descending evidence_consistency_score.
   - Tie-breaking hierarchy:
     1. Higher evidence_availability_ratio
     2. Smaller spatial discrepancy (km)
     3. Smaller temporal discrepancy (hours)
     4. Stable vessel identifier (lexicographical)

5. SCIENTIFIC LANGUAGE:
   - "evidence_consistency_score"
   - NOT probability of responsibility, guilt, causation, or legal evidence.
   - "Ranked by consistency with available evidence."

6. ZERO FABRICATION:
   - No synthetic AIS positions, no track interpolation, no intent inference.
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
from app.models.evidence_fusion import (
    BehavioralContextSummary,
    EvidenceFusionResult,
    EvidenceSignal,
    ForwardDriftCrossCheck,
    SignalStatus,
    VesselFusedEvidence,
)
from app.services.drift_modelling import _sanitize


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CandidateRankingError(Exception):
    """Raised when Stage F2 candidate ranking fails or inputs are insufficient."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NOMINAL_WEIGHTS: dict[str, float] = {
    "spatial": 0.50,
    "temporal": 0.25,
    "trajectory": 0.25,
}

_CHANNEL_KEY_MAP: dict[str, str] = {
    "spatial_proximity": "spatial",
    "temporal_proximity": "temporal",
    "trajectory_consistency": "trajectory",
}


# ---------------------------------------------------------------------------
# Scoring and Discrepancy Helpers
# ---------------------------------------------------------------------------


def _validate_weights(weights: dict[str, float] | None) -> dict[str, float]:
    """Validate and normalize candidate scoring weights.

    Ensures all weights are non-negative (>= 0.0) and their sum is strictly positive (> 0.0).
    """
    if weights is None:
        return dict(NOMINAL_WEIGHTS)

    if not isinstance(weights, dict):
        raise CandidateRankingError(f"Weights must be a dictionary, got {type(weights).__name__}")

    cleaned: dict[str, float] = {}
    for k, v in weights.items():
        try:
            val = float(v)
        except (ValueError, TypeError):
            raise CandidateRankingError(f"Weight for channel '{k}' must be a valid float, got {v}")
        if val < 0.0:
            raise CandidateRankingError(f"Weight for channel '{k}' must be non-negative (>= 0.0), got {val}")
        cleaned[k] = val

    total = sum(cleaned.values())
    if total <= 0.0:
        raise CandidateRankingError("Total sum of nominal scoring weights must be strictly positive (> 0.0).")

    return cleaned


def calculate_candidate_score_profile(
    fev: VesselFusedEvidence,
    nominal_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Calculate the deterministic evidence consistency score and detailed profile for a candidate.

    Computes:
        S_candidate = sum(w_i * S_i) / sum(w_i) for all available channels.
    Channels with missing/insufficient data are excluded from both numerator and denominator.
    If no channels are available, score is None (never 0.0).

    Returns a comprehensive dictionary with score, weights applied, availability, and components.
    """
    weights_config = _validate_weights(nominal_weights)
    total_primary_channels = len(weights_config)

    # Map signals by channel
    signals_by_channel: dict[str, EvidenceSignal] = {}
    for sig in fev.primary_signals:
        mapped_key = _CHANNEL_KEY_MAP.get(sig.channel_name, sig.channel_name)
        signals_by_channel[mapped_key] = sig

    # Resolve scores per channel, prioritizing valid signals or rich normalized_evidence
    channel_scores: dict[str, float | None] = {}

    # 1. Spatial
    sp_sig = signals_by_channel.get("spatial")
    sp_score = sp_sig.score if (sp_sig and sp_sig.status == SignalStatus.VALID) else None
    if sp_score is None and fev.normalized_evidence and fev.normalized_evidence.spatial_proximity is not None:
        sp_score = fev.normalized_evidence.spatial_proximity
    channel_scores["spatial"] = sp_score

    # 2. Temporal
    tp_sig = signals_by_channel.get("temporal")
    tp_score = tp_sig.score if (tp_sig and tp_sig.status == SignalStatus.VALID) else None
    if tp_score is None and fev.normalized_evidence and fev.normalized_evidence.temporal_proximity is not None:
        tp_score = fev.normalized_evidence.temporal_proximity
    channel_scores["temporal"] = tp_score

    # 3. Trajectory
    tr_sig = signals_by_channel.get("trajectory")
    tr_score = tr_sig.score if (tr_sig and tr_sig.status == SignalStatus.VALID) else None
    if tr_score is None and fev.normalized_evidence and fev.normalized_evidence.trajectory_alignment is not None:
        tr_score = fev.normalized_evidence.trajectory_alignment
    channel_scores["trajectory"] = tr_score

    # Determine valid channels among configured weights
    valid_channels: list[tuple[str, float, float]] = []
    available_dimensions: list[str] = []
    unavailable_dimensions: list[str] = []

    for ch_name, weight in weights_config.items():
        score = channel_scores.get(ch_name)
        if score is not None:
            valid_channels.append((ch_name, weight, score))
            available_dimensions.append(ch_name)
        else:
            unavailable_dimensions.append(ch_name)

    valid_primary_channels = len(valid_channels)
    evidence_availability_ratio = (
        round(valid_primary_channels / total_primary_channels, 6) if total_primary_channels > 0 else 0.0
    )

    channel_weights_applied: dict[str, float] = {}
    active_weight_sum = 0.0

    if valid_channels:
        active_weight_sum = sum(weight for _, weight, _ in valid_channels)
        if active_weight_sum > 0.0:
            numerator = sum(weight * score for _, weight, score in valid_channels)
            evidence_consistency_score = round(numerator / active_weight_sum, 6)
            for ch_name, weight, _ in valid_channels:
                channel_weights_applied[ch_name] = round(weight / active_weight_sum, 6)
        else:
            evidence_consistency_score = None
    else:
        evidence_consistency_score = None

    return {
        "evidence_consistency_score": evidence_consistency_score,
        "valid_primary_channels": valid_primary_channels,
        "total_primary_channels": total_primary_channels,
        "evidence_availability_ratio": evidence_availability_ratio,
        "channel_weights_applied": channel_weights_applied,
        "spatial_score": channel_scores.get("spatial"),
        "temporal_score": channel_scores.get("temporal"),
        "trajectory_score": channel_scores.get("trajectory"),
        "active_weight_sum": round(active_weight_sum, 6),
        "available_dimensions": available_dimensions,
        "unavailable_dimensions": unavailable_dimensions,
        "raw_score_components": {
            "spatial": channel_scores.get("spatial"),
            "temporal": channel_scores.get("temporal"),
            "trajectory": channel_scores.get("trajectory"),
        },
    }


def calculate_candidate_score(
    fev: VesselFusedEvidence,
    nominal_weights: dict[str, float] | None = None,
) -> tuple[float | None, int, int, float, dict[str, float], float | None, float | None, float | None]:
    """Calculate the deterministic evidence consistency score for a candidate.

    Returns the standard 8-tuple for backward compatibility:
        (
            evidence_consistency_score,
            valid_primary_channels,
            total_primary_channels,
            evidence_availability_ratio,
            channel_weights_applied,
            spatial_score,
            temporal_score,
            trajectory_score,
        )
    """
    profile = calculate_candidate_score_profile(fev, nominal_weights)
    return (
        profile["evidence_consistency_score"],
        profile["valid_primary_channels"],
        profile["total_primary_channels"],
        profile["evidence_availability_ratio"],
        profile["channel_weights_applied"],
        profile["spatial_score"],
        profile["temporal_score"],
        profile["trajectory_score"],
    )


def extract_discrepancies(
    fev: VesselFusedEvidence,
) -> tuple[float | None, float | None]:
    """Extract spatial (km) and temporal (hours) discrepancies for tie-breaking.

    Returns:
        (spatial_discrepancy_km, temporal_discrepancy_hours)
    """
    spatial_disp: float | None = None
    temporal_disp: float | None = None

    # Check rich structured F1 evidence first if available
    if fev.evidence is not None:
        if fev.evidence.spatial is not None:
            spatial_disp = fev.evidence.spatial.min_distance_to_center_km
        if fev.evidence.temporal is not None:
            temporal_disp = fev.evidence.temporal.time_offset_from_source_hours

    # Fallback to primary_signals raw_metrics
    if spatial_disp is None or temporal_disp is None:
        for sig in fev.primary_signals:
            mapped_key = _CHANNEL_KEY_MAP.get(sig.channel_name, sig.channel_name)
            raw = sig.raw_metrics or {}

            if mapped_key == "spatial" and spatial_disp is None:
                if "d_center_km" in raw and raw["d_center_km"] is not None:
                    spatial_disp = float(raw["d_center_km"])
                elif "d_boundary_km" in raw and raw["d_boundary_km"] is not None:
                    spatial_disp = float(raw["d_boundary_km"])

            elif mapped_key == "temporal" and temporal_disp is None:
                if "delta_t_hours" in raw and raw["delta_t_hours"] is not None:
                    temporal_disp = float(raw["delta_t_hours"])
                elif "time_offset_from_source_hours" in raw and raw["time_offset_from_source_hours"] is not None:
                    temporal_disp = abs(float(raw["time_offset_from_source_hours"]))

    return spatial_disp, temporal_disp


def _build_limitations(
    valid_channels: int,
    total_channels: int,
    contextual_evidence: BehavioralContextSummary,
    drift_cross_check: ForwardDriftCrossCheck,
) -> list[str]:
    """Assemble factual scientific limitations for a ranked candidate."""
    limitations: list[str] = []

    if valid_channels < total_channels:
        limitations.append(
            f"{total_channels - valid_channels} of {total_channels} primary evidence channel(s) unavailable; "
            "weights dynamically renormalized."
        )

    if contextual_evidence.total_anomalies_count > 0:
        limitations.append(
            "E3 behavioral anomalies are contextual only and do not contribute to the consistency score."
        )

    if not drift_cross_check.evaluated:
        limitations.append(
            "D1 forward drift cross-check was not evaluated; primary consistency score is unaffected."
        )

    limitations.append(
        "Evidence consistency score is a physical compatibility index, not a probability of discharge or legal responsibility."
    )

    return limitations


# ---------------------------------------------------------------------------
# Sorting Key for Deterministic Tie-Breaking
# ---------------------------------------------------------------------------


def _ranking_sort_key(candidate: RankedCandidate) -> tuple:
    """Generate deterministic sort key for descending rank ordering.

    Hierarchy:
    1. Score validity & value: descending (-has_score, -score)
    2. Evidence availability ratio: descending (-ratio)
    3. Spatial discrepancy: ascending (smaller distance is closer)
    4. Temporal discrepancy: ascending (smaller time offset is closer)
    5. Stable vessel identifier: ascending (lexicographical)
    6. Candidate ID: ascending (lexicographical)
    """
    has_score = 1 if candidate.evidence_consistency_score is not None else 0
    score_val = (
        round(candidate.evidence_consistency_score, 6) if candidate.evidence_consistency_score is not None else -1.0
    )
    ratio_val = round(candidate.evidence_availability_ratio, 6)

    spatial_disp = candidate.provenance.get("spatial_discrepancy_km")
    spatial_val = spatial_disp if spatial_disp is not None else float("inf")

    temporal_disp = candidate.provenance.get("temporal_discrepancy_hours")
    temporal_val = temporal_disp if temporal_disp is not None else float("inf")

    vessel_ident = str(candidate.vessel_id or "")
    candidate_ident = str(candidate.candidate_id or "")

    return (
        -has_score,
        -score_val,
        -ratio_val,
        spatial_val,
        temporal_val,
        vessel_ident,
        candidate_ident,
    )


# ---------------------------------------------------------------------------
# Main Ranking Function
# ---------------------------------------------------------------------------


def rank_candidates(
    evidence_fusion: EvidenceFusionResult,
    investigation_id: str | None = None,
    spill_id: str | None = None,
    output_dir: str | Path | None = None,
    registry: AssetRegistry | None = None,
    weights: dict[str, float] | None = None,
) -> tuple[CandidateRanking, Asset]:
    """Execute Stage F2 Candidate Scoring & Ranking.

    Consumes Stage F1 EvidenceFusionResult, calculates the Evidence Consistency Score
    per candidate vessel using dynamically renormalized primary weights, and produces
    a strictly deterministic comparative ranking.

    Args:
        evidence_fusion: Upstream Stage F1 evidence fusion result.
        investigation_id: Optional override for investigation ID.
        spill_id: Optional override for spill detection ID.
        output_dir: Optional override directory for ranking artifacts.
        registry: Target AssetRegistry for registering derived DOCUMENT asset.
        weights: Optional custom nominal weights for primary channels.

    Returns:
        (CandidateRanking, Asset)

    Raises:
        CandidateRankingError: If inputs are invalid or inconsistent.
    """
    if evidence_fusion is None:
        raise CandidateRankingError("Cannot rank candidates: evidence_fusion input is None.")

    active_weights_config = _validate_weights(weights)

    target_inv = investigation_id or evidence_fusion.investigation_id
    target_spill = spill_id or evidence_fusion.spill_detection_id
    target_registry = registry or default_asset_registry

    now_utc = datetime.now(timezone.utc)
    res_id = f"ranking-{_sanitize(target_spill, 'spill')}-{_sanitize(target_inv, 'inv')}"

    # Process all candidates from F1
    unranked_candidates: list[RankedCandidate] = []

    for fev in evidence_fusion.fused_candidates:
        profile = calculate_candidate_score_profile(fev, nominal_weights=active_weights_config)
        score = profile["evidence_consistency_score"]
        valid_channels = profile["valid_primary_channels"]
        total_channels = profile["total_primary_channels"]
        avail_ratio = profile["evidence_availability_ratio"]
        weights_applied = profile["channel_weights_applied"]
        spatial_s = profile["spatial_score"]
        temporal_s = profile["temporal_score"]
        trajectory_s = profile["trajectory_score"]
        active_w_sum = profile["active_weight_sum"]
        avail_dims = profile["available_dimensions"]
        unavail_dims = profile["unavailable_dimensions"]
        raw_components = profile["raw_score_components"]

        spatial_disp, temporal_disp = extract_discrepancies(fev)
        limitations = _build_limitations(
            valid_channels,
            total_channels,
            fev.behavioral_context,
            fev.forward_drift_cross_check,
        )

        provenance: dict[str, Any] = {
            "candidate_id": fev.candidate_id,
            "vessel_id": fev.vessel_id,
            "input_index_f1": fev.input_index,
            "f1_composite_concordance_score": fev.composite_concordance_score,
            "spatial_discrepancy_km": spatial_disp,
            "temporal_discrepancy_hours": temporal_disp,
            "channel_weights_applied": weights_applied,
            "behavioral_anomalies_count": fev.behavioral_context.total_anomalies_count,
            "behavioral_score_contribution": 0.0,
            "forward_drift_score_contribution": 0.0,
            "zero_fabrication": True,
        }
        if fev.provenance is not None:
            provenance["is_real_observation"] = fev.provenance.is_real_observation
            provenance["data_source_type"] = fev.provenance.data_source_type
            provenance["source_assets"] = fev.provenance.source_assets
            provenance["timestamps"] = fev.provenance.timestamps
            provenance["fallback_flags"] = fev.provenance.fallback_flags
        else:
            provenance["is_real_observation"] = fev.metadata.get("is_real_observation", False)
            provenance["data_source_type"] = fev.metadata.get("data_source_type", "curated_historical_reconstruction")

        uncertainty_summary: dict[str, Any] = {}
        if fev.uncertainty is not None:
            uncertainty_summary = fev.uncertainty.model_dump()
        else:
            uncertainty_summary = {
                "source_fallback_applied": fev.metadata.get("current_fallback", False),
                "shoreline_truncated": fev.metadata.get("shoreline_terminated", False),
            }

        cand_warnings = list(fev.warnings) if fev.warnings else []
        if valid_channels == 0:
            cand_warnings.append("No primary evidence channels were available for scoring.")
        elif valid_channels < total_channels:
            cand_warnings.append(
                f"{total_channels - valid_channels} of {total_channels} primary channels unavailable; weights renormalized."
            )

        # Extract IMO if recorded in metadata
        imo_val = fev.metadata.get("imo")

        candidate = RankedCandidate(
            rank=1,  # Temporary, assigned after sorting
            vessel_id=fev.vessel_id,
            candidate_id=fev.candidate_id,
            mmsi=fev.mmsi,
            imo=imo_val,
            name=fev.vessel_name,
            vessel_type=fev.vessel_type,
            evidence_consistency_score=score,
            spatial_score=spatial_s,
            temporal_score=temporal_s,
            trajectory_score=trajectory_s,
            valid_primary_channels=valid_channels,
            total_primary_channels=total_channels,
            evidence_availability_ratio=avail_ratio,
            contextual_evidence=fev.behavioral_context,
            drift_cross_check=fev.forward_drift_cross_check,
            limitations=limitations,
            provenance=provenance,
            raw_score_components=raw_components,
            active_weights=weights_applied,
            active_weight_sum=active_w_sum,
            available_dimensions=avail_dims,
            unavailable_dimensions=unavail_dims,
            uncertainty_summary=uncertainty_summary,
            ranking_explanation_metadata={
                "spatial_discrepancy_km": spatial_disp,
                "temporal_discrepancy_hours": temporal_disp,
                "score_interpretation": "Dimensionless physical consistency index in [0, 1]. Not a probability.",
            },
            warnings=cand_warnings,
        )
        unranked_candidates.append(candidate)

    # Sort deterministically
    sorted_candidates = sorted(unranked_candidates, key=_ranking_sort_key)

    # Assign 1-based ranks and detect exact ties
    ranked_candidates: list[RankedCandidate] = []
    for idx, cand in enumerate(sorted_candidates):
        cand_dict = cand.model_dump()
        cand_dict["rank"] = idx + 1

        is_tied = False
        tie_reasons: list[str] = []
        if (
            idx > 0
            and cand.evidence_consistency_score is not None
            and sorted_candidates[idx - 1].evidence_consistency_score == cand.evidence_consistency_score
        ):
            is_tied = True
            tie_reasons.append("Identical score with preceding candidate; tie broken by secondary criteria.")
        if (
            idx + 1 < len(sorted_candidates)
            and cand.evidence_consistency_score is not None
            and sorted_candidates[idx + 1].evidence_consistency_score == cand.evidence_consistency_score
        ):
            is_tied = True
            tie_reasons.append("Identical score with succeeding candidate; tie broken by secondary criteria.")

        cand_dict["ranking_explanation_metadata"]["is_tied_score"] = is_tied
        if is_tied:
            cand_dict["ranking_explanation_metadata"]["tie_notes"] = tie_reasons

        ranked_candidates.append(RankedCandidate.model_validate(cand_dict))

    top_warnings = list(evidence_fusion.warnings) if evidence_fusion.warnings else []

    # Build CandidateRanking model
    ranking_result = CandidateRanking(
        id=res_id,
        investigation_id=target_inv,
        spill_id=target_spill,
        evidence_fusion_id=evidence_fusion.id,
        generated_at=now_utc,
        methodology_version="F2-1.0.0",
        ranking_method="availability_weighted_concordance",
        score_version="F2-1.0.0",
        deterministic_tie_break_rule=(
            "score_desc -> availability_ratio_desc -> spatial_disp_asc -> "
            "temporal_disp_asc -> vessel_id_asc -> candidate_id_asc"
        ),
        nominal_weights=active_weights_config,
        candidate_count=len(ranked_candidates),
        candidates=ranked_candidates,
        warnings=top_warnings,
        metadata={
            "stage": "F2",
            "asset_type": "candidate_ranking",
            "zero_fabrication": True,
            "methodology": "Stage F2 Candidate Scoring & Ranking",
            "scoring_formula": (
                "weighted average of valid primary channels: "
                f"{', '.join(f'{k}={v:.2f}' for k, v in active_weights_config.items())} with dynamic denominator renormalization"
            ),
            "tie_breaking_order": [
                "1. evidence_consistency_score (descending)",
                "2. evidence_availability_ratio (descending)",
                "3. spatial_discrepancy_km (ascending)",
                "4. temporal_discrepancy_hours (ascending)",
                "5. vessel_id (lexicographical ascending)",
                "6. candidate_id (lexicographical ascending)",
            ],
            "scientific_notice": (
                "Ranked by physical consistency with available evidence. "
                "Evidence consistency score is a compatibility index in [0, 1], "
                "not a probability of responsibility, guilt, or legal liability."
            ),
            "behavioral_contribution": 0.0,
            "forward_drift_contribution": 0.0,
            "is_real_observation": all(c.provenance.get("is_real_observation", False) for c in ranked_candidates) if ranked_candidates else False,
            "data_source_type": ranked_candidates[0].provenance.get("data_source_type", "curated_historical_reconstruction") if ranked_candidates else "unknown",
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
            / "candidate_ranking"
            / safe_spill
        )

    target_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = target_dir / "candidate_ranking.json"
    artifact_path.write_text(ranking_result.model_dump_json(indent=2) + "\n", encoding="utf-8")

    # Register derived DOCUMENT asset
    artifact = AcquiredArtifact(
        asset_type=AssetType.DOCUMENT,
        location=str(artifact_path.resolve()),
        source="candidate_ranking_service",
        acquisition_time=now_utc,
        provenance=Provenance(
            product_id=res_id,
            retrieved_at=now_utc,
            processing_level="candidate_ranking",
            notes=(
                f"Stage F2 candidate scoring & ranking for {len(ranked_candidates)} vessels. "
                f"Spill={target_spill}, EvidenceFusion={evidence_fusion.id}. Zero-fabrication guarantee."
            ),
            extra={
                "asset_type": "candidate_ranking",
                "stage": "F2",
                "spill_detection_id": target_spill,
                "evidence_fusion_id": evidence_fusion.id,
                "ranked_candidate_count": len(ranked_candidates),
                "zero_fabrication": True,
            },
        ),
        metadata={
            "asset_type": "candidate_ranking",
            "stage": "F2",
            "investigation_id": target_inv,
            "spill_id": target_spill,
            "evidence_fusion_id": evidence_fusion.id,
            "ranked_candidate_count": len(ranked_candidates),
            "zero_fabrication": True,
        },
    )

    derived_asset = target_registry.register(
        target_inv, "candidate_ranking_service", artifact
    )
    ranking_result.asset_id = derived_asset.id

    return ranking_result, derived_asset


# ---------------------------------------------------------------------------
# Loader Helper
# ---------------------------------------------------------------------------


def load_evidence_fusion_from_asset(
    asset: Asset,
    registry: AssetRegistry | None = None,
) -> EvidenceFusionResult:
    """Load or reconstruct an EvidenceFusionResult from an asset."""
    artifact_path = Path(asset.location)
    if not artifact_path.exists():
        raise CandidateRankingError(f"Evidence fusion artifact does not exist: {artifact_path}")

    # Check for direct JSON
    json_path = artifact_path.with_suffix(".json") if artifact_path.suffix == ".geojson" else artifact_path
    if json_path.exists() and json_path.name.endswith(".json"):
        try:
            return EvidenceFusionResult.model_validate_json(json_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Reconstruct from GeoJSON FeatureCollection
    try:
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CandidateRankingError(f"Malformed evidence fusion artifact: {exc}") from exc

    props = data.get("properties", {})
    features = data.get("features", [])

    inv_id = props.get("investigation_id", asset.investigation_id)
    spill_id = props.get("spill_detection_id") or asset.metadata.get("spill_id", "unknown-spill")
    source_est_id = props.get("source_estimate_id") or asset.metadata.get("source_estimate_id", "unknown-source")

    fused_candidates: list[VesselFusedEvidence] = []
    for feat in features:
        fprops = feat.get("properties", {})
        if fprops.get("feature_kind") != "evidence_fusion_candidate":
            continue

        c_id = fprops.get("candidate_id", "cand-unknown")
        v_id = fprops.get("vessel_id", "vessel-unknown")
        mmsi = fprops.get("mmsi")
        name = fprops.get("vessel_name")
        input_idx = fprops.get("input_index", len(fused_candidates))

        spatial_s = fprops.get("spatial_score")
        temporal_s = fprops.get("temporal_score")
        trajectory_s = fprops.get("trajectory_score")

        spatial_sig = EvidenceSignal(
            channel_name="spatial_proximity",
            status=SignalStatus.VALID if spatial_s is not None else SignalStatus.INSUFFICIENT_DATA,
            score=spatial_s,
            nominal_weight=0.50,
            raw_metrics={"d_center_km": fprops.get("spatial_discrepancy_km")},
            rationale="Reconstructed from GeoJSON artifact.",
        )
        temporal_sig = EvidenceSignal(
            channel_name="temporal_proximity",
            status=SignalStatus.VALID if temporal_s is not None else SignalStatus.INSUFFICIENT_DATA,
            score=temporal_s,
            nominal_weight=0.25,
            raw_metrics={"delta_t_hours": fprops.get("temporal_discrepancy_hours")},
            rationale="Reconstructed from GeoJSON artifact.",
        )
        trajectory_sig = EvidenceSignal(
            channel_name="trajectory_consistency",
            status=SignalStatus.VALID if trajectory_s is not None else SignalStatus.INSUFFICIENT_DATA,
            score=trajectory_s,
            nominal_weight=0.25,
            raw_metrics={},
            rationale="Reconstructed from GeoJSON artifact.",
        )

        b_ctx = BehavioralContextSummary(
            total_anomalies_count=fprops.get("behavioral_total_anomalies", 0),
            loitering_detected=fprops.get("behavioral_loitering_detected", False),
            transmission_gaps_count=fprops.get("behavioral_gaps_count", 0),
            gaps_spanning_zone_count=fprops.get("behavioral_gaps_spanning_zone", 0),
        )

        fwd_check = ForwardDriftCrossCheck(
            evaluated=fprops.get("forward_drift_cross_check_evaluated", False),
            min_distance_to_forward_track_km=fprops.get("forward_drift_min_distance_km"),
        )

        fev = VesselFusedEvidence(
            mmsi=mmsi,
            vessel_name=name,
            candidate_id=c_id,
            vessel_id=v_id,
            input_index=input_idx,
            composite_concordance_score=fprops.get("composite_concordance_score"),
            evidence_availability_ratio=fprops.get("evidence_availability_ratio", 0.0),
            primary_signals=[spatial_sig, temporal_sig, trajectory_sig],
            behavioral_context=b_ctx,
            forward_drift_cross_check=fwd_check,
            metadata={"zero_fabrication": True},
        )
        fused_candidates.append(fev)

    res_id = props.get("evidence_fusion_id") or asset.provenance.product_id or asset.id

    return EvidenceFusionResult(
        id=res_id,
        investigation_id=inv_id,
        spill_detection_id=spill_id,
        source_estimate_id=source_est_id,
        candidate_generation_id=asset.metadata.get("candidate_generation_id", "unknown-cg"),
        trajectory_analysis_id=asset.metadata.get("trajectory_analysis_id", "unknown-ta"),
        behavioral_intelligence_id=asset.metadata.get("behavioral_intelligence_id", "unknown-bi"),
        forward_drift_id=asset.metadata.get("forward_drift_id"),
        asset_id=asset.id,
        normalization_reference_radius_km=props.get("normalization_reference_radius_km", 10.0),
        temporal_scale_hours=props.get("temporal_scale_hours", 2.0),
        nominal_weights=props.get("nominal_weights", {"spatial_proximity": 0.50, "temporal_proximity": 0.25, "trajectory_consistency": 0.25}),
        candidate_count=len(fused_candidates),
        fused_candidates=fused_candidates,
        metadata={"zero_fabrication": True},
    )
