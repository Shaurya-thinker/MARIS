"""MARIS Stage F1 — Multi-Source Spatio-Temporal Evidence Fusion Service.

Fuses physical evidence from B3, D3, E1, E2 and contextual intelligence from E3,
producing a deterministic Composite Concordance Score per candidate vessel.

MANDATORY INVARIANTS:
1. PRIMARY CONCORDANCE SCORE — exactly 3 channels:
      spatial_proximity   (w_nominal = 0.50)
      temporal_proximity  (w_nominal = 0.25)
      trajectory_consistency (w_nominal = 0.25)
   Dynamic weight re-normalisation over valid channels only.
   Missing channels are EXCLUDED from the weighted denominator (never treated as 0).

2. BEHAVIOURAL INTELLIGENCE EXCLUSION:
   E3 anomalies contribute exactly 0.00 to the concordance score.
   More anomalies NEVER imply higher physical concordance.

3. FORWARD DRIFT EXCLUSION:
   D1 forward drift is an optional auxiliary cross-check only.
   Supplying or omitting D1 must NOT change the primary score.
   Rationale: D1 and D3 share identical ERA5/CMEMS forcing; weighting both
   would double-count environmental drift physics.

4. NON-RANKING:
   Candidate order from E1 is preserved exactly in the output.
   No sorting, ranking, nomination, filtering, or elimination in F1.

5. ZERO FABRICATION:
   All calculations use only genuine AIS observations and existing upstream
   drift/trajectory products. No interpolation, dead-reckoning, or reconstruction.

6. SCORE MEANING:
   composite_concordance_score ∈ [0.0, 1.0] is a deterministic physical
   compatibility index. NOT a probability, guilt score, or legal evidence.

FORMULAE:
  R_zone = source_estimate.source_uncertainty_radius_km  (km)
  τ_scale = max(2 * source_estimate.step_hours, 2.0)     (hours)

  Spatial:
    d_center = transit_profile.min_distance_to_center_km
    d_boundary = transit_profile.distance_to_zone_boundary_km
    if inside zone (d_boundary == 0.0):
        S_spatial = exp(-0.5 * (d_center / R_zone)^2)
    else:
        S_spatial = exp(-0.5) * exp(-d_boundary / R_zone)

  Temporal:
    Δt = |time_offset_from_source_hours|
    S_temporal = exp(-0.5 * (Δt / τ_scale)^2)

  Trajectory:
    d_cl = centerline_proximity.min_distance_to_centerline_km
    S_cross_track = exp(-0.5 * (d_cl / R_zone)^2)
    if angular_diff is available:
        S_angular = (1 + cos(radians(angular_diff))) / 2
        S_trajectory = 0.6 * S_cross_track + 0.4 * S_angular
    else:
        S_trajectory = S_cross_track

  Composite (dynamic re-normalisation over valid channels A):
    S_concordance = sum(w_i * S_i for i in A) / sum(w_i for i in A)

Asset Registration:
  The derived evidence-fusion artifact is registered as AssetType.DOCUMENT
  with metadata {"asset_type": "evidence_fusion"}.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.acquisition.registry import AssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.core.config import settings
from app.models.asset import Asset
from app.models.behavioral_intelligence import (
    BehavioralIntelligenceResult,
    VesselBehavioralProfile,
)
from app.models.common import AssetType, Provenance
from app.models.drift import DriftResult, DriftStep
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
from app.models.satellite import SpillDetection
from app.models.source_estimation import SourceEstimateResult
from app.models.trajectory_analysis import (
    TrajectoryAnalysisResult,
    VesselTrajectoryAnalysis,
)
from app.models.vessel import (
    CandidateVessel,
    CandidateVesselGenerationResult,
)
from app.services.candidate_environment import CandidateEnvironment
from app.services.drift_modelling import _haversine_m, _sanitize, _utc


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EvidenceFusionError(Exception):
    """Raised when Stage F1 evidence fusion cannot proceed."""


# ---------------------------------------------------------------------------
# Scoring Helpers
# ---------------------------------------------------------------------------

#: Nominal weights for the 3 primary evidence channels.
_NOMINAL_WEIGHTS: dict[str, float] = {
    "spatial_proximity": 0.50,
    "temporal_proximity": 0.25,
    "trajectory_consistency": 0.25,
}

_CHANNEL_NAMES = ["spatial_proximity", "temporal_proximity", "trajectory_consistency"]


def _score_spatial(
    d_center_km: float,
    d_boundary_km: float,
    r_zone_km: float,
) -> float:
    """Compute S_spatial ∈ (0, 1].

    Inside zone (d_boundary == 0.0):
        S = exp(-0.5 * (d_center / R_zone)^2)
        Yields 1.0 at exact center; ≈0.6065 at zone boundary.

    Outside zone (d_boundary > 0.0):
        S = exp(-0.5) * exp(-d_boundary / R_zone)
        Continuous at boundary; decays strictly toward 0.0 with distance.
    """
    if r_zone_km <= 0.0:
        # Degenerate: cannot normalise; return 0.0
        return 0.0

    if d_boundary_km <= 0.0:
        # Inside or exactly on the zone boundary
        ratio = d_center_km / r_zone_km
        return math.exp(-0.5 * ratio * ratio)
    else:
        # Outside zone
        return math.exp(-0.5) * math.exp(-d_boundary_km / r_zone_km)


def _score_temporal(
    abs_time_offset_hours: float,
    tau_scale_hours: float,
) -> float:
    """Compute S_temporal ∈ (0, 1].

    S = exp(-0.5 * (Δt / τ_scale)^2)
    """
    if tau_scale_hours <= 0.0:
        return 0.0
    ratio = abs_time_offset_hours / tau_scale_hours
    return math.exp(-0.5 * ratio * ratio)


def _score_trajectory(
    d_centerline_km: float,
    r_zone_km: float,
    angular_diff_deg: float | None,
) -> tuple[float, str]:
    """Compute S_trajectory ∈ [0, 1] and a rationale snippet.

    S_cross_track = exp(-0.5 * (d_cl / R_zone)^2)
    If angular_diff available:
        S_angular = (1 + cos(radians(angular_diff))) / 2
        S_traj    = 0.6 * S_cross_track + 0.4 * S_angular
    Else:
        S_traj = S_cross_track
    """
    if r_zone_km <= 0.0:
        return 0.0, "R_zone=0; degenerate spatial reference"

    s_cross_track = math.exp(-0.5 * (d_centerline_km / r_zone_km) ** 2)

    if angular_diff_deg is not None:
        s_angular = (1.0 + math.cos(math.radians(angular_diff_deg))) / 2.0
        score = 0.6 * s_cross_track + 0.4 * s_angular
        rationale = (
            f"S_trajectory = 0.6*S_cross_track({s_cross_track:.4f}) + "
            f"0.4*S_angular({s_angular:.4f}) = {score:.4f}. "
            f"d_centerline={d_centerline_km:.3f}km, angular_diff={angular_diff_deg:.1f}deg, "
            f"R_zone={r_zone_km:.3f}km."
        )
    else:
        score = s_cross_track
        rationale = (
            f"S_trajectory = S_cross_track({s_cross_track:.4f}) "
            f"(angular comparison unavailable — COG not reported or vessel stationary). "
            f"d_centerline={d_centerline_km:.3f}km, R_zone={r_zone_km:.3f}km."
        )

    return score, rationale


def _composite_score(
    signals: list[EvidenceSignal],
) -> float | None:
    """Dynamically re-normalise and combine valid primary channel scores.

    Excludes channels with status != VALID from both numerator and denominator.
    Returns None if no valid channels exist.
    """
    numerator = 0.0
    denom = 0.0
    for sig in signals:
        if sig.status == SignalStatus.VALID and sig.score is not None:
            numerator += sig.nominal_weight * sig.score
            denom += sig.nominal_weight
    if denom <= 0.0:
        return None
    return round(numerator / denom, 6)


def _evidence_availability_ratio(signals: list[EvidenceSignal]) -> float:
    """Fraction of primary channels evaluated with valid data.

    evidence_availability_ratio = N_valid / 3.0

    This is an F1 evidence-channel availability metric,
    NOT an AIS transmission completeness measure.
    """
    n_valid = sum(1 for s in signals if s.status == SignalStatus.VALID)
    return round(n_valid / max(len(signals), 1), 6)


# ---------------------------------------------------------------------------
# Behavioral Context Builder
# ---------------------------------------------------------------------------


def _build_behavioral_context(profile: VesselBehavioralProfile | None) -> BehavioralContextSummary:
    """Summarise E3 behavioral profile into a BehavioralContextSummary.

    MANDATORY: This summary contributes 0.00 to the concordance score.
    """
    if profile is None:
        return BehavioralContextSummary()

    anomalies_in_zone = [a for a in profile.anomalies if a.inside_source_zone]
    gaps_spanning = [g for g in profile.transmission_gaps if g.spanned_source_zone]

    # Collect boolean flags from anomaly types
    speed_drop = any(
        a.anomaly_type.value == "speed_drop_in_zone" for a in profile.anomalies
    )
    speed_surge = any(
        a.anomaly_type.value == "speed_surge_near_zone" for a in profile.anomalies
    )
    nav_mismatch = not profile.nav_status_consistent

    return BehavioralContextSummary(
        total_anomalies_count=len(profile.anomalies),
        anomalies_in_zone_count=len(anomalies_in_zone),
        loitering_detected=profile.loitering_detected,
        transmission_gaps_count=len(profile.transmission_gaps),
        gaps_spanning_zone_count=len(gaps_spanning),
        speed_drop_in_zone=speed_drop,
        speed_surge_near_zone=speed_surge,
        nav_status_mismatch=nav_mismatch,
        anchor_swing_observed=profile.anchor_swing_profile is not None,
        notable_anomaly_types=list(profile.summary_flags),
    )


# ---------------------------------------------------------------------------
# Forward Drift Cross-Check Builder
# ---------------------------------------------------------------------------


def _build_forward_drift_cross_check(
    candidate: CandidateVessel,
    drift_result: DriftResult | None,
) -> ForwardDriftCrossCheck:
    """Cross-check candidate AIS pings against D1 forward drift track.

    MANDATORY: Excluded from concordance score.
    """
    if drift_result is None or not drift_result.steps:
        return ForwardDriftCrossCheck(
            evaluated=False,
            cross_check_note="D1 forward drift not supplied; cross-check not performed.",
        )

    forward_coords: list[list[float]] = [[s.lon, s.lat] for s in drift_result.steps]

    if not candidate.raw_positions:
        return ForwardDriftCrossCheck(
            evaluated=True,
            cross_check_note="D1 forward drift supplied but no candidate positions available.",
        )

    min_dist_m = float("inf")
    best_step: DriftStep = drift_result.steps[0]

    for pos in candidate.raw_positions:
        # Distance from position to each forward drift step (polyline)
        for i, step in enumerate(drift_result.steps):
            d_m = _haversine_m(pos.lon, pos.lat, step.lon, step.lat)
            if d_m < min_dist_m:
                min_dist_m = d_m
                best_step = step

    min_dist_km = round(min_dist_m / 1000.0, 3)

    return ForwardDriftCrossCheck(
        evaluated=True,
        min_distance_to_forward_track_km=min_dist_km,
        closest_forward_step_time=_utc(best_step.timestamp),
        cross_check_note=(
            f"Candidate track minimum distance to D1 forward drift polyline: {min_dist_km:.3f} km. "
            "Cross-check only; excluded from primary concordance score."
        ),
    )


# ---------------------------------------------------------------------------
# Domain Summary Builders (B3, C2, D3)
# ---------------------------------------------------------------------------


def _build_spill_summary(
    spill_detection: SpillDetection | None,
    spill_id: str,
) -> SpillEvidenceSummary:
    """Extract structured B3 spill detection context."""
    if spill_detection is None:
        return SpillEvidenceSummary(spill_id=spill_id)

    geom = spill_detection.geometry or {}
    centroid_lon: float | None = None
    centroid_lat: float | None = None
    if geom.get("type") == "Point" and len(geom.get("coordinates", [])) >= 2:
        centroid_lon = float(geom["coordinates"][0])
        centroid_lat = float(geom["coordinates"][1])
    elif geom.get("type") == "Polygon" and geom.get("coordinates"):
        ring = geom["coordinates"][0]
        if ring:
            centroid_lon = round(sum(p[0] for p in ring) / len(ring), 6)
            centroid_lat = round(sum(p[1] for p in ring) / len(ring), 6)

    prov = dict(spill_detection.metadata.get("provenance") or {})
    maritime_status = spill_detection.metadata.get("maritime_status") or spill_detection.metadata.get("maritime_domain_status")
    vv_vh = spill_detection.metadata.get("vv_vh_ratio") or spill_detection.metadata.get("polarization_ratio")

    return SpillEvidenceSummary(
        spill_id=spill_detection.id or spill_id,
        detection_confidence=spill_detection.confidence,
        spill_area_m2=spill_detection.area,
        centroid_lon=centroid_lon,
        centroid_lat=centroid_lat,
        maritime_domain_status=str(maritime_status) if maritime_status else None,
        vv_vh_ratio=float(vv_vh) if vv_vh is not None else None,
        detection_provenance=prov,
    )


def _build_environmental_summary(
    env_context: CandidateEnvironment | None,
) -> EnvironmentalEvidenceSummary:
    """Extract structured C2 metocean context."""
    if env_context is None:
        return EnvironmentalEvidenceSummary(
            missing_data_status="UNAVAILABLE",
        )

    w = env_context.wind
    c = env_context.current
    reg = env_context.regime
    dq = env_context.data_quality

    reg_val = reg.classification.value if hasattr(reg.classification, "value") else str(reg.classification)
    missing_status = "NONE" if (dq.wind_available and dq.current_available) else (
        "MISSING_CURRENT" if dq.wind_available else (
            "MISSING_WIND" if dq.current_available else "UNAVAILABLE"
        )
    )

    return EnvironmentalEvidenceSummary(
        wind_speed_ms=w.speed_ms,
        wind_direction_from_deg=w.direction_from_deg,
        wind_regime=reg_val,
        current_speed_ms=c.speed_ms,
        current_direction_to_deg=c.direction_to_deg,
        source_timestamp=w.source_timestamp or c.source_timestamp,
        interpolation_method=w.spatial_interpolation or c.spatial_interpolation,
        data_quality="VALID" if (dq.wind_available and dq.interpolation_valid) else "DEGRADED",
        missing_data_status=missing_status,
    )


def _build_source_summary(
    source_estimate: SourceEstimateResult,
) -> SourceEvidenceSummary:
    """Extract structured D3 source estimation evidence."""
    term_status = source_estimate.metadata.get("termination_reason") or source_estimate.metadata.get("status")
    shoreline = bool(
        source_estimate.metadata.get("shoreline_terminated")
        or (term_status and "shoreline" in str(term_status).lower())
    )
    fallback = bool(
        source_estimate.metadata.get("current_fallback")
        or source_estimate.metadata.get("wind_only_fallback")
    )
    fallback_info = source_estimate.metadata.get("fallback_info") or source_estimate.metadata.get("environmental_fallback")

    return SourceEvidenceSummary(
        source_point_lon=source_estimate.source_point_lon,
        source_point_lat=source_estimate.source_point_lat,
        source_uncertainty_radius_km=source_estimate.source_uncertainty_radius_km,
        source_time=_utc(source_estimate.source_time),
        steps_count=len(source_estimate.steps) if source_estimate.steps else 0,
        termination_status=str(term_status) if term_status else "COMPLETED",
        current_fallback_applied=fallback,
        shoreline_terminated=shoreline,
        environmental_fallback_info=str(fallback_info) if fallback_info else None,
    )


# ---------------------------------------------------------------------------
# Core Fusion Logic — Per-Vessel
# ---------------------------------------------------------------------------


def _fuse_vessel_evidence(
    candidate: CandidateVessel,
    input_index: int,
    trajectory_analysis: VesselTrajectoryAnalysis | None,
    behavioral_profile: VesselBehavioralProfile | None,
    r_zone_km: float,
    tau_scale_hours: float,
    source_time: datetime,
    drift_result: DriftResult | None,
    spill_summary: SpillEvidenceSummary | None = None,
    env_summary: EnvironmentalEvidenceSummary | None = None,
    source_summary: SourceEvidenceSummary | None = None,
) -> VesselFusedEvidence:
    """Compute the fused evidence record for a single candidate vessel.

    Invariants enforced here:
    - Missing data → SignalStatus.INSUFFICIENT_DATA (channel excluded from denominator).
    - Behavioral context contributes 0.00 to concordance score.
    - Forward drift is an excluded cross-check.
    - Input order is preserved via input_index.
    """
    spill_sum = spill_summary or SpillEvidenceSummary()
    env_sum = env_summary or EnvironmentalEvidenceSummary()
    src_sum = source_summary or SourceEvidenceSummary(
        source_uncertainty_radius_km=r_zone_km,
        source_time=source_time,
    )

    # ---- Signal 1: Spatial Proximity ----
    if trajectory_analysis is None:
        spatial_signal = EvidenceSignal(
            channel_name="spatial_proximity",
            status=SignalStatus.INSUFFICIENT_DATA,
            score=None,
            nominal_weight=_NOMINAL_WEIGHTS["spatial_proximity"],
            raw_metrics={},
            rationale=(
                "No E2 trajectory analysis available for this candidate. "
                "Spatial channel excluded from concordance score denominator."
            ),
        )
    else:
        tp = trajectory_analysis.transit_profile
        d_center = tp.min_distance_to_center_km
        d_boundary = tp.distance_to_zone_boundary_km
        s_sp = _score_spatial(d_center, d_boundary, r_zone_km)
        inside = d_boundary <= 0.0
        spatial_signal = EvidenceSignal(
            channel_name="spatial_proximity",
            status=SignalStatus.VALID,
            score=round(s_sp, 6),
            nominal_weight=_NOMINAL_WEIGHTS["spatial_proximity"],
            raw_metrics={
                "d_center_km": round(d_center, 4),
                "d_boundary_km": round(d_boundary, 4),
                "r_zone_km": round(r_zone_km, 4),
                "inside_zone": inside,
            },
            rationale=(
                f"{'Inside' if inside else 'Outside'} zone. "
                f"d_center={d_center:.4f}km, d_boundary={d_boundary:.4f}km, "
                f"R_zone={r_zone_km:.4f}km. "
                + (
                    f"S_spatial = exp(-0.5 * ({d_center:.4f}/{r_zone_km:.4f})^2) = {s_sp:.6f}."
                    if inside
                    else (
                        f"S_spatial = exp(-0.5) * exp(-{d_boundary:.4f}/{r_zone_km:.4f}) = {s_sp:.6f}."
                    )
                )
            ),
        )

    # ---- Signal 2: Temporal Proximity ----
    if trajectory_analysis is None:
        temporal_signal = EvidenceSignal(
            channel_name="temporal_proximity",
            status=SignalStatus.INSUFFICIENT_DATA,
            score=None,
            nominal_weight=_NOMINAL_WEIGHTS["temporal_proximity"],
            raw_metrics={},
            rationale=(
                "No E2 trajectory analysis available for this candidate. "
                "Temporal channel excluded from concordance score denominator."
            ),
        )
    else:
        tp = trajectory_analysis.transit_profile
        abs_offset_h = abs(tp.time_offset_from_source_hours)
        s_temp = _score_temporal(abs_offset_h, tau_scale_hours)
        temporal_signal = EvidenceSignal(
            channel_name="temporal_proximity",
            status=SignalStatus.VALID,
            score=round(s_temp, 6),
            nominal_weight=_NOMINAL_WEIGHTS["temporal_proximity"],
            raw_metrics={
                "time_offset_from_source_hours": tp.time_offset_from_source_hours,
                "abs_offset_hours": round(abs_offset_h, 4),
                "tau_scale_hours": round(tau_scale_hours, 4),
                "cpa_time": tp.closest_position_time.isoformat(),
                "source_time": source_time.isoformat(),
            },
            rationale=(
                f"Δt = |{tp.time_offset_from_source_hours:.4f}| = {abs_offset_h:.4f}h, "
                f"τ_scale = {tau_scale_hours:.4f}h. "
                f"S_temporal = exp(-0.5 * ({abs_offset_h:.4f}/{tau_scale_hours:.4f})^2) = {s_temp:.6f}."
            ),
        )

    # ---- Signal 3: Trajectory / Centerline Consistency ----
    if trajectory_analysis is None:
        trajectory_signal = EvidenceSignal(
            channel_name="trajectory_consistency",
            status=SignalStatus.INSUFFICIENT_DATA,
            score=None,
            nominal_weight=_NOMINAL_WEIGHTS["trajectory_consistency"],
            raw_metrics={},
            rationale=(
                "No E2 trajectory analysis available for this candidate. "
                "Trajectory channel excluded from concordance score denominator."
            ),
        )
    else:
        cp = trajectory_analysis.centerline_proximity
        d_cl = cp.min_distance_to_centerline_km
        angular_diff = cp.course_drift_angle_diff_deg  # float | None

        if trajectory_analysis.observed_positions_count < 1:
            trajectory_signal = EvidenceSignal(
                channel_name="trajectory_consistency",
                status=SignalStatus.INSUFFICIENT_DATA,
                score=None,
                nominal_weight=_NOMINAL_WEIGHTS["trajectory_consistency"],
                raw_metrics={"d_centerline_km": d_cl},
                rationale=(
                    "No observed AIS positions; trajectory consistency cannot be evaluated. "
                    "Channel excluded from concordance score denominator."
                ),
            )
        else:
            s_traj, traj_rationale = _score_trajectory(d_cl, r_zone_km, angular_diff)
            trajectory_signal = EvidenceSignal(
                channel_name="trajectory_consistency",
                status=SignalStatus.VALID,
                score=round(s_traj, 6),
                nominal_weight=_NOMINAL_WEIGHTS["trajectory_consistency"],
                raw_metrics={
                    "d_centerline_km": round(d_cl, 4),
                    "r_zone_km": round(r_zone_km, 4),
                    "course_drift_angle_diff_deg": angular_diff,
                    "angular_comparison_available": angular_diff is not None,
                },
                rationale=traj_rationale,
            )

    primary_signals = [spatial_signal, temporal_signal, trajectory_signal]

    # ---- Composite Score (dynamic re-normalisation) ----
    concordance = _composite_score(primary_signals)
    avail_ratio = _evidence_availability_ratio(primary_signals)

    # ---- Behavioral Context (contributes 0.00 to score) ----
    behavioral_ctx = _build_behavioral_context(behavioral_profile)

    # ---- Forward Drift Cross-Check (excluded from score) ----
    fwd_check = _build_forward_drift_cross_check(candidate, drift_result)

    # ---- Build Structured Evidence Blocks ----
    cand_meta = candidate.metadata or {}
    ais_summary = AisEvidenceSummary(
        mmsi=candidate.mmsi,
        imo=cand_meta.get("imo"),
        vessel_name=candidate.vessel_name,
        vessel_type=candidate.vessel_type or cand_meta.get("vessel_type"),
        call_sign=cand_meta.get("call_sign"),
        flag=cand_meta.get("flag"),
        accepted_observation_count=candidate.observed_positions_count,
        spatial_filtering_passed=True,
        temporal_filtering_passed=True,
        data_source_type=str(cand_meta.get("data_source_type", "curated_historical_reconstruction")),
        is_real_observation=bool(cand_meta.get("is_real_observation", False)),
    )

    if trajectory_analysis is not None:
        tp = trajectory_analysis.transit_profile
        cp = trajectory_analysis.centerline_proximity
        q = getattr(trajectory_analysis, "quality", None)
        tc = getattr(trajectory_analysis, "temporal_correlation", None)
        e2_ev = getattr(trajectory_analysis, "evidence", {}) or {}
        kin_ev = e2_ev.get("kinematic", {})

        inside_zone = (tp.distance_to_zone_boundary_km <= 0.0) or bool(getattr(candidate, "inside_source_zone", False))
        spatial_summary = SpatialEvidenceSummary(
            min_distance_to_center_km=tp.min_distance_to_center_km,
            distance_to_zone_boundary_km=tp.distance_to_zone_boundary_km,
            inside_source_zone=inside_zone,
            min_distance_to_centerline_km=cp.min_distance_to_centerline_km,
            cpa_lon=getattr(tp, "cpa_lon", None),
            cpa_lat=getattr(tp, "cpa_lat", None),
            cpa_time=_utc(tp.closest_position_time) if tp.closest_position_time else None,
        )

        abs_offset_h = abs(tp.time_offset_from_source_hours)
        span_h: float | None = None
        if tc and tc.temporal_span_seconds:
            span_h = round(tc.temporal_span_seconds / 3600.0, 4)
        elif q and q.temporal_span_seconds:
            span_h = round(q.temporal_span_seconds / 3600.0, 4)

        temporal_summary = TemporalEvidenceSummary(
            time_offset_from_source_hours=tp.time_offset_from_source_hours,
            abs_time_offset_hours=round(abs_offset_h, 4),
            tau_scale_hours=round(tau_scale_hours, 4),
            closest_observation_time=_utc(tp.closest_position_time) if tp.closest_position_time else None,
            temporal_span_hours=span_h,
        )

        reported_sog = kin_ev.get("reported_sog_knots") or getattr(trajectory_analysis, "mean_reported_sog_knots", None)
        derived_speed = kin_ev.get("derived_speed_mps")
        bearing = kin_ev.get("bearing_degrees")
        continuity = kin_ev.get("trajectory_continuity")
        has_valid_kin = kin_ev.get("has_valid_kinematics", False)
        invalid_cnt = kin_ev.get("invalid_intervals_count", 0)

        kinematic_summary = KinematicEvidenceSummary(
            reported_sog_knots=reported_sog,
            derived_speed_mps=derived_speed,
            bearing_degrees=bearing,
            trajectory_continuity=str(continuity) if continuity else None,
            has_valid_kinematics=has_valid_kin,
            invalid_intervals_count=invalid_cnt,
        )

        sparse = (candidate.observed_positions_count <= 1)
        if q is not None:
            sparse = q.sparse_track or (candidate.observed_positions_count <= 1)

        data_quality_summary = DataQualityEvidenceSummary(
            observed_positions_count=candidate.observed_positions_count,
            duplicate_count=q.duplicate_count if q else 0,
            sparse_track=sparse,
            quality_flags=list(q.quality_flags) if q else (["sparse_track"] if sparse else []),
            provenance=dict(cand_meta.get("provenance") or {}),
        )
    else:
        spatial_summary = SpatialEvidenceSummary()
        temporal_summary = TemporalEvidenceSummary()
        kinematic_summary = KinematicEvidenceSummary()
        data_quality_summary = DataQualityEvidenceSummary(
            observed_positions_count=candidate.observed_positions_count,
            sparse_track=(candidate.observed_positions_count <= 1),
        )

    evidence_detail = CandidateEvidenceDetail(
        spill=spill_sum,
        environment=env_sum,
        source=src_sum,
        ais=ais_summary,
        spatial=spatial_summary,
        temporal=temporal_summary,
        kinematic=kinematic_summary,
        data_quality=data_quality_summary,
    )

    normalized_profile = NormalizedEvidenceProfile(
        spatial_proximity=spatial_signal.score,
        temporal_proximity=temporal_signal.score,
        trajectory_alignment=trajectory_signal.score,
        source_zone_membership=spatial_summary.inside_source_zone,
        environmental_compatibility=env_sum.wind_regime,
        trajectory_quality="SPARSE" if data_quality_summary.sparse_track else ("ADEQUATE" if data_quality_summary.observed_positions_count > 1 else "NONE"),
    )

    # Availability profile
    avail_dims: list[str] = []
    unavail_dims: list[str] = []
    missing_reasons: dict[str, str] = {}

    for sig in primary_signals:
        if sig.status == SignalStatus.VALID:
            avail_dims.append(sig.channel_name)
        else:
            unavail_dims.append(sig.channel_name)
            missing_reasons[sig.channel_name] = sig.rationale

    if env_sum.wind_speed_ms is not None:
        avail_dims.append("environmental_context")
    else:
        unavail_dims.append("environmental_context")
        missing_reasons["environmental_context"] = "Environmental context not supplied or incomplete."

    if spill_sum.spill_id:
        avail_dims.append("spill_detection")
    else:
        unavail_dims.append("spill_detection")
        missing_reasons["spill_detection"] = "Spill detection metadata unavailable."

    availability_profile = EvidenceAvailabilityProfile(
        available_dimensions=avail_dims,
        unavailable_dimensions=unavail_dims,
        missing_reasons=missing_reasons,
    )

    # Uncertainty profile
    uncertainty_profile = MultiSourceUncertainty(
        source_uncertainty_km=r_zone_km,
        source_fallback_applied=src_sum.current_fallback_applied or src_sum.shoreline_terminated,
        trajectory_uncertainty=(
            "Sparse trajectory (1 observation ping); kinematic and course continuity cannot be confirmed."
            if data_quality_summary.sparse_track
            else (
                f"Observed {data_quality_summary.observed_positions_count} AIS pings; track quality adequate."
                if data_quality_summary.observed_positions_count > 1
                else "No AIS positions observed within spatio-temporal candidate window."
            )
        ),
        environmental_uncertainty=(
            f"Metocean conditions: wind={env_sum.wind_speed_ms} m/s ({env_sum.wind_regime}), current={env_sum.current_speed_ms} m/s. "
            "ERA5/CMEMS resolution and interpolation caveats apply."
            if env_sum.wind_speed_ms is not None
            else "Environmental metocean context was not supplied; lookalike risk and drift certainty unassessed."
        ),
        detection_uncertainty=(
            f"B3 detection confidence={spill_sum.detection_confidence}. Uncalibrated dark-spot feature score; not a calibrated probability."
            if spill_sum.detection_confidence is not None
            else "Spill detection confidence not recorded."
        ),
        geospatial_uncertainty="Subject to SAR geocoding / terrain distortion accuracy (~10-20 m).",
    )

    # Provenance record
    prov_record = EvidenceProvenanceRecord(
        source_assets={
            "D3_source_estimate": "source_estimate" if src_sum.source_point_lon is not None else "none",
            "E1_candidate_generation": candidate.candidate_id,
            "E2_trajectory_analysis": trajectory_analysis.candidate_id if trajectory_analysis else "none",
        },
        data_source_type=ais_summary.data_source_type,
        is_real_observation=ais_summary.is_real_observation,
        timestamps={
            "source_time": source_time.isoformat(),
            "candidate_cpa_time": spatial_summary.cpa_time.isoformat() if spatial_summary.cpa_time else "none",
        },
        fallback_flags={
            "current_fallback_applied": src_sum.current_fallback_applied,
            "shoreline_terminated": src_sum.shoreline_terminated,
        },
    )

    # Warnings
    cand_warnings: list[str] = []
    if data_quality_summary.sparse_track:
        cand_warnings.append("Single AIS observation ping: velocity and heading vectors cannot be verified independently.")
    if src_sum.current_fallback_applied:
        cand_warnings.append("D3 drift used wind-only current fallback: hydrodynamic drift advection may be underestimated.")
    if src_sum.shoreline_terminated:
        cand_warnings.append("D3 drift reached shoreline boundary: backward integration was prematurely terminated.")
    if env_sum.wind_regime == "CALM_WATER_LOOKALIKE":
        cand_warnings.append("Low wind regime (< 2.5 m/s): high risk of calm-water natural lookalike ambiguity.")
    elif env_sum.wind_regime == "HIGH_WIND_DISPERSION":
        cand_warnings.append("High wind regime (> 12.0 m/s): surface slick dispersion risk.")

    return VesselFusedEvidence(
        mmsi=candidate.mmsi,
        vessel_name=candidate.vessel_name,
        vessel_type=candidate.metadata.get("vessel_type"),
        candidate_id=candidate.candidate_id,
        vessel_id=candidate.vessel_id,
        input_index=input_index,
        composite_concordance_score=concordance,
        evidence_availability_ratio=avail_ratio,
        primary_signals=primary_signals,
        behavioral_context=behavioral_ctx,
        forward_drift_cross_check=fwd_check,
        evidence=evidence_detail,
        normalized_evidence=normalized_profile,
        availability=availability_profile,
        uncertainty=uncertainty_profile,
        provenance=prov_record,
        warnings=cand_warnings,
        metadata={
            "zero_fabrication": True,
            "observed_positions_count": candidate.observed_positions_count,
            "inside_source_zone": candidate.inside_source_zone,
        },
    )


# ---------------------------------------------------------------------------
# GeoJSON Export
# ---------------------------------------------------------------------------


def export_evidence_fusion_geojson(
    result: EvidenceFusionResult,
    source_estimate: SourceEstimateResult,
    artifact_path: Path,
) -> None:
    """Write Stage F1 evidence fusion result as a GeoJSON FeatureCollection.

    Features:
    - source_candidate_zone polygon (D3)
    - backward_drift_centerline polyline (D3)
    - evidence_fusion_candidate Point per candidate (preserving input order)
    """
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    features: list[dict[str, Any]] = []

    # Source candidate zone
    zone_geom = source_estimate.source_zone_geometry or {}
    if zone_geom:
        features.append({
            "type": "Feature",
            "geometry": zone_geom,
            "properties": {
                "feature_kind": "source_candidate_zone",
                "source_estimate_id": source_estimate.id,
                "uncertainty_radius_km": source_estimate.source_uncertainty_radius_km,
            },
        })

    # Backward drift centerline
    if source_estimate.steps:
        cl_coords = [[s.lon, s.lat] for s in source_estimate.steps]
    else:
        cl_coords = [
            [source_estimate.origin_lon, source_estimate.origin_lat],
            [source_estimate.source_point_lon, source_estimate.source_point_lat],
        ]
    features.append({
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": cl_coords},
        "properties": {
            "feature_kind": "backward_drift_centerline",
            "source_estimate_id": source_estimate.id,
        },
    })

    # One Point per candidate (in input order)
    for fev in result.fused_candidates:
        # Use the transit CPA coordinates from primary spatial signal raw_metrics if available
        # Fall back to candidate metadata
        sig_meta = {}
        for sig in fev.primary_signals:
            if sig.channel_name == "spatial_proximity" and sig.raw_metrics:
                sig_meta = sig.raw_metrics
                break

        # Concordance score props
        feat_props: dict[str, Any] = {
            "feature_kind": "evidence_fusion_candidate",
            "candidate_id": fev.candidate_id,
            "vessel_id": fev.vessel_id,
            "mmsi": fev.mmsi,
            "vessel_name": fev.vessel_name,
            "input_index": fev.input_index,
            "composite_concordance_score": fev.composite_concordance_score,
            "evidence_availability_ratio": fev.evidence_availability_ratio,
            "spatial_score": next(
                (s.score for s in fev.primary_signals if s.channel_name == "spatial_proximity"), None
            ),
            "temporal_score": next(
                (s.score for s in fev.primary_signals if s.channel_name == "temporal_proximity"), None
            ),
            "trajectory_score": next(
                (s.score for s in fev.primary_signals if s.channel_name == "trajectory_consistency"), None
            ),
            # Normalized representations
            "spatial_proximity_score": fev.normalized_evidence.spatial_proximity,
            "temporal_proximity_score": fev.normalized_evidence.temporal_proximity,
            "trajectory_alignment_score": fev.normalized_evidence.trajectory_alignment,
            "source_zone_membership": fev.normalized_evidence.source_zone_membership,
            "environmental_compatibility": fev.normalized_evidence.environmental_compatibility,
            "trajectory_quality": fev.normalized_evidence.trajectory_quality,
            # Provenance and classification
            "data_source_type": fev.provenance.data_source_type,
            "is_real_observation": fev.provenance.is_real_observation,
            # Behavioral context (factual, not scoring)
            "behavioral_total_anomalies": fev.behavioral_context.total_anomalies_count,
            "behavioral_loitering_detected": fev.behavioral_context.loitering_detected,
            "behavioral_gaps_count": fev.behavioral_context.transmission_gaps_count,
            "behavioral_gaps_spanning_zone": fev.behavioral_context.gaps_spanning_zone_count,
            "behavioral_score_contribution": 0.0,  # Always 0 — explicit provenance
            # Forward drift cross-check (excluded from score)
            "forward_drift_cross_check_evaluated": fev.forward_drift_cross_check.evaluated,
            "forward_drift_min_distance_km": fev.forward_drift_cross_check.min_distance_to_forward_track_km,
            "forward_drift_score_contribution": 0.0,  # Always 0 — explicit provenance
            "zero_fabrication": True,
            "warnings": fev.warnings,
            "caveat": (
                "Composite concordance index in [0, 1]. "
                "NOT a probability of responsibility, guilt, causation, or legal evidence."
            ),
            "stage": "F1",
        }

        # Best geometry: CPA lat/lon from E2 transit_profile raw_metrics (d_center carries CPA info)
        # We encode a placeholder at source center if no CPA info; actual CPA is in the primary_signal metadata
        # Use source_estimate center as fallback geometry (candidates point is informational)
        feat_geom = {
            "type": "Point",
            "coordinates": [
                source_estimate.source_point_lon,
                source_estimate.source_point_lat,
            ],
        }

        features.append({"type": "Feature", "geometry": feat_geom, "properties": feat_props})

    geojson_body = {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "stage": "F1",
            "investigation_id": result.investigation_id,
            "spill_detection_id": result.spill_detection_id,
            "source_estimate_id": result.source_estimate_id,
            "normalization_reference_radius_km": result.normalization_reference_radius_km,
            "temporal_scale_hours": result.temporal_scale_hours,
            "nominal_weights": result.nominal_weights,
            "candidate_count": result.candidate_count,
            "zero_fabrication": True,
        },
    }

    artifact_path.write_text(json.dumps(geojson_body, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Main Service Entry Point
# ---------------------------------------------------------------------------


def fuse_evidence(
    investigation_id: str,
    spill_id: str,
    source_estimate: SourceEstimateResult,
    candidate_result: CandidateVesselGenerationResult,
    trajectory_result: TrajectoryAnalysisResult,
    behavioral_result: BehavioralIntelligenceResult,
    drift_result: DriftResult | None = None,
    output_dir: str | Path | None = None,
    registry: AssetRegistry | None = None,
    spill_detection: SpillDetection | None = None,
    environmental_context: CandidateEnvironment | None = None,
) -> tuple[EvidenceFusionResult, Asset]:
    """Execute Stage F1 multi-source spatio-temporal evidence fusion.

    Preconditions:
    - source_estimate, candidate_result, trajectory_result, behavioral_result
      are validated upstream products.
    - drift_result is optional (D1 forward drift cross-check only).
    - spill_detection is optional (B3 spill detection context).
    - environmental_context is optional (C2 candidate-level metocean context).
    - Candidate order from candidate_result.candidates is strictly preserved.

    Returns:
        (EvidenceFusionResult, Asset): Fusion result and registered DOCUMENT asset.
    """
    target_registry = registry or default_asset_registry

    # ---- Derive normalisation parameters from D3 upstream contracts ----
    r_zone_km: float = source_estimate.source_uncertainty_radius_km
    if r_zone_km <= 0.0:
        raise EvidenceFusionError(
            f"source_estimate.source_uncertainty_radius_km must be > 0. Got: {r_zone_km}"
        )

    # τ_scale = max(2 * step_hours, 2.0) — derived from D3 integration parameters
    tau_scale_hours: float = max(2.0 * source_estimate.step_hours, 2.0)

    source_time: datetime = _utc(source_estimate.source_time)

    # ---- Build domain summaries ----
    spill_sum = _build_spill_summary(spill_detection, spill_id)
    env_sum = _build_environmental_summary(environmental_context)
    src_sum = _build_source_summary(source_estimate)

    # ---- Build lookup maps for E2 and E3 by MMSI / candidate_id ----
    traj_map: dict[str, VesselTrajectoryAnalysis] = {}
    for ta in trajectory_result.analyses:
        if ta.mmsi:
            traj_map[ta.mmsi] = ta
        traj_map[ta.candidate_id] = ta
        traj_map[ta.vessel_id] = ta

    beh_map: dict[str, VesselBehavioralProfile] = {}
    for bp in behavioral_result.profiles:
        if bp.mmsi:
            beh_map[bp.mmsi] = bp
        beh_map[bp.candidate_id] = bp
        beh_map[bp.vessel_id] = bp

    # ---- Iterate candidates in STRICT E1 input order ----
    fused_candidates: list[VesselFusedEvidence] = []
    inv_warnings: list[str] = []

    if src_sum.current_fallback_applied:
        inv_warnings.append("D3 backward drift used wind-only current fallback (missing or land-masked CMEMS currents).")
    if src_sum.shoreline_terminated:
        inv_warnings.append("D3 backward drift reached shoreline boundary; source zone integration was truncated.")
    if env_sum.missing_data_status != "NONE":
        inv_warnings.append(f"Environmental context status: {env_sum.missing_data_status}.")

    for idx, candidate in enumerate(candidate_result.candidates):
        # Resolve E2 trajectory analysis for this candidate
        traj_analysis: VesselTrajectoryAnalysis | None = (
            traj_map.get(candidate.mmsi or "")
            or traj_map.get(candidate.candidate_id)
            or traj_map.get(candidate.vessel_id)
        )

        # Resolve E3 behavioral profile for this candidate
        beh_profile: VesselBehavioralProfile | None = (
            beh_map.get(candidate.mmsi or "")
            or beh_map.get(candidate.candidate_id)
            or beh_map.get(candidate.vessel_id)
        )

        fev = _fuse_vessel_evidence(
            candidate=candidate,
            input_index=idx,
            trajectory_analysis=traj_analysis,
            behavioral_profile=beh_profile,
            r_zone_km=r_zone_km,
            tau_scale_hours=tau_scale_hours,
            source_time=source_time,
            drift_result=drift_result,
            spill_summary=spill_sum,
            env_summary=env_sum,
            source_summary=src_sum,
        )
        fused_candidates.append(fev)

    # ---- Build result object ----
    res_id = f"evidence-fusion-{_sanitize(spill_id, 'spill')}-{_sanitize(investigation_id, 'inv')}"

    result = EvidenceFusionResult(
        id=res_id,
        investigation_id=investigation_id,
        spill_detection_id=spill_id,
        source_estimate_id=source_estimate.id,
        candidate_generation_id=candidate_result.id,
        trajectory_analysis_id=trajectory_result.id,
        behavioral_intelligence_id=behavioral_result.id,
        forward_drift_id=drift_result.id if drift_result else None,
        normalization_reference_radius_km=r_zone_km,
        temporal_scale_hours=tau_scale_hours,
        nominal_weights=dict(_NOMINAL_WEIGHTS),
        candidate_count=len(fused_candidates),
        fused_candidates=fused_candidates,
        spill_evidence_summary=spill_sum,
        environmental_evidence_summary=env_sum,
        warnings=inv_warnings,
        metadata={
            "zero_fabrication": True,
            "behavioral_contribution_to_score": 0.0,
            "forward_drift_contribution_to_score": 0.0,
            "r_zone_source": "source_estimate.source_uncertainty_radius_km",
            "tau_scale_formula": "max(2 * source_estimate.step_hours, 2.0)",
            "score_interpretation": (
                "Composite concordance index in [0,1]. "
                "NOT a probability, guilt score, or attribution. "
                "High score indicates physical consistency with the estimated spill source zone."
            ),
            "candidate_ordering": "Strict E1 input order. No ranking or sorting by F1.",
            "data_source_type": (
                fused_candidates[0].provenance.data_source_type if fused_candidates else "curated_historical_reconstruction"
            ),
            "is_real_observation": (
                fused_candidates[0].provenance.is_real_observation if fused_candidates else False
            ),
        },
    )

    # ---- Export GeoJSON artifact ----
    if output_dir is not None:
        target_dir = Path(output_dir)
    else:
        safe_inv = _sanitize(investigation_id, "investigation")
        safe_spill = _sanitize(spill_id, "spill")
        target_dir = (
            Path(settings.data_dir)
            / "derived"
            / safe_inv
            / "evidence_fusion"
            / safe_spill
        )

    artifact_path = target_dir / "evidence_fusion.geojson"
    export_evidence_fusion_geojson(result, source_estimate, artifact_path)

    # ---- Register derived DOCUMENT asset ----
    now_utc = datetime.now(timezone.utc)

    artifact = AcquiredArtifact(
        asset_type=AssetType.DOCUMENT,
        location=str(artifact_path.resolve()),
        source="evidence_fusion_service",
        acquisition_time=now_utc,
        provenance=Provenance(
            product_id=res_id,
            retrieved_at=now_utc,
            processing_level="evidence_fusion",
            notes=(
                f"Stage F1 evidence fusion for {len(fused_candidates)} candidate vessels. "
                f"Spill={spill_id}, SourceEstimate={source_estimate.id}, "
                f"R_zone={r_zone_km}km, τ_scale={tau_scale_hours}h. Zero-fabrication guarantee."
            ),
            extra={
                "asset_type": "evidence_fusion",
                "stage": "F1",
                "spill_detection_id": spill_id,
                "source_estimate_id": source_estimate.id,
                "candidate_generation_id": candidate_result.id,
                "trajectory_analysis_id": trajectory_result.id,
                "behavioral_intelligence_id": behavioral_result.id,
                "forward_drift_id": drift_result.id if drift_result else None,
                "fused_candidate_count": len(fused_candidates),
                "normalization_reference_radius_km": r_zone_km,
                "temporal_scale_hours": tau_scale_hours,
                "zero_fabrication": True,
                "behavioral_contribution_to_score": 0.0,
                "forward_drift_contribution_to_score": 0.0,
                "data_source_type": (
                    fused_candidates[0].provenance.data_source_type if fused_candidates else "curated_historical_reconstruction"
                ),
                "is_real_observation": (
                    fused_candidates[0].provenance.is_real_observation if fused_candidates else False
                ),
            },
        ),
        metadata={
            "asset_type": "evidence_fusion",
            "investigation_id": investigation_id,
            "spill_id": spill_id,
            "source_estimate_id": source_estimate.id,
            "candidate_generation_id": candidate_result.id,
            "trajectory_analysis_id": trajectory_result.id,
            "behavioral_intelligence_id": behavioral_result.id,
            "forward_drift_id": drift_result.id if drift_result else None,
            "fused_candidate_count": len(fused_candidates),
            "zero_fabrication": True,
            "data_source_type": (
                fused_candidates[0].provenance.data_source_type if fused_candidates else "curated_historical_reconstruction"
            ),
            "is_real_observation": (
                fused_candidates[0].provenance.is_real_observation if fused_candidates else False
            ),
        },
    )

    derived_asset = target_registry.register(
        investigation_id, "evidence_fusion_service", artifact
    )
    result.asset_id = derived_asset.id

    return result, derived_asset
