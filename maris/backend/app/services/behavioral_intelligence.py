"""MARIS Stage E3 — Behavioural Intelligence Service.

Computes deterministic behavioural and operational anomaly intelligence for candidate
vessels identified in Stage E1 and analyzed in Stage E2.

MANDATORY CONSTRAINTS:
1. AIS gaps must be reported only as observable transmission gaps. Never call them
   intentional "dark vessel" events and never infer transponder disabling or activity during the gap.
2. LOITERING_OBSERVED must mean only an observed low-speed, multi-course-change pattern
   in genuine AIS observations. Never infer intent, suspiciousness, discharge activity, or culpability.
3. Anchor-swing analysis must describe only the observed positional envelope/centroid from
   genuine AIS observations. Do not claim an exact physical anchor position or exact swing circle
   when sparse observations cannot support that precision.
4. ZERO-FABRICATION INVARIANT: Never interpolate, dead-reckon, reconstruct, or infer missing AIS positions.
5. EXCLUSION OF STAGE F: No attribution, evidence fusion, responsibility, or polluter ranking.
6. NO ML: Deterministic, transparent, rule-based operational anomaly indicators.

Asset Registration:
The derived behavioral-intelligence artifact is registered as AssetType.DOCUMENT with
metadata {"asset_type": "behavioral_intelligence"}.
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
    AnchorSwingProfile,
    AnomalySeverity,
    BehavioralAnomaly,
    BehavioralAnomalyType,
    BehavioralIntelligenceResult,
    TransmissionGap,
    VesselBehavioralProfile,
)
from app.models.common import AssetType, Provenance
from app.models.source_estimation import SourceEstimateResult
from app.models.vessel import (
    CandidateVessel,
    CandidateVesselGenerationResult,
    VesselPosition,
)
from app.services.candidate_vessels import (
    load_source_estimate_from_asset,
    point_in_polygon,
)
from app.services.drift_modelling import _haversine_m, _sanitize, _utc
from app.services.trajectory_analysis import (
    angular_difference_deg,
    load_candidate_result_from_asset,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class BehavioralIntelligenceError(Exception):
    """Raised when behavioural intelligence analysis fails."""


# ---------------------------------------------------------------------------
# Helper Utilities
# ---------------------------------------------------------------------------

def is_point_near_or_in_zone(
    lon: float,
    lat: float,
    polygon_ring: list[list[float]],
    center_lon: float,
    center_lat: float,
    radius_km: float,
    buffer_km: float = 2.0,
) -> bool:
    """Check if point is inside polygon or within radius + buffer from center."""
    if polygon_ring and point_in_polygon(lon, lat, polygon_ring):
        return True
    dist_km = _haversine_m(lon, lat, center_lon, center_lat) / 1000.0
    return dist_km <= (radius_km + buffer_km)


def normalize_nav_status(status: str | None) -> str | None:
    """Normalize reported navigation status string or numeric code."""
    if status is None:
        return None
    s = str(status).strip().lower()
    mapping = {
        "0": "under_way_engine",
        "under way using engine": "under_way_engine",
        "under_way": "under_way_engine",
        "1": "at_anchor",
        "at anchor": "at_anchor",
        "at_anchor": "at_anchor",
        "2": "not_under_command",
        "not under command": "not_under_command",
        "3": "restricted_manoeuvrability",
        "5": "moored",
        "moored": "moored",
        "8": "under_way_sailing",
        "sailing": "under_way_sailing",
    }
    return mapping.get(s, s)


# ---------------------------------------------------------------------------
# Anomaly Detection Algorithms
# ---------------------------------------------------------------------------

def detect_transmission_gaps(
    positions: list[VesselPosition],
    polygon_ring: list[list[float]],
    center_lon: float,
    center_lat: float,
    radius_km: float,
    gap_threshold_seconds: float = 1800.0,
) -> tuple[list[TransmissionGap], list[BehavioralAnomaly]]:
    """Detect observable gaps between consecutive genuine AIS observations.

    Zero-fabrication invariant:
    - Reports observable gap facts only.
    - Never infers intentional disabling, dark activity, or vessel path during gap.
    """
    gaps: list[TransmissionGap] = []
    anomalies: list[BehavioralAnomaly] = []

    if len(positions) < 2:
        return gaps, anomalies

    for i in range(len(positions) - 1):
        p1 = positions[i]
        p2 = positions[i + 1]

        dt = (p2.timestamp - p1.timestamp).total_seconds()
        if dt >= gap_threshold_seconds:
            dist_km = _haversine_m(p1.lon, p1.lat, p2.lon, p2.lat) / 1000.0

            # Check if gap spans near/in source zone
            near_p1 = is_point_near_or_in_zone(p1.lon, p1.lat, polygon_ring, center_lon, center_lat, radius_km)
            near_p2 = is_point_near_or_in_zone(p2.lon, p2.lat, polygon_ring, center_lon, center_lat, radius_km)
            spanned = near_p1 or near_p2

            # Also check midpoint
            if not spanned:
                mid_lon = 0.5 * (p1.lon + p2.lon)
                mid_lat = 0.5 * (p1.lat + p2.lat)
                spanned = is_point_near_or_in_zone(mid_lon, mid_lat, polygon_ring, center_lon, center_lat, radius_km)

            gap_obj = TransmissionGap(
                gap_start_time=p1.timestamp,
                gap_end_time=p2.timestamp,
                gap_duration_seconds=round(dt, 1),
                gap_start_lon=p1.lon,
                gap_start_lat=p1.lat,
                gap_end_lon=p2.lon,
                gap_end_lat=p2.lat,
                distance_across_gap_km=round(dist_km, 3),
                spanned_source_zone=spanned,
            )
            gaps.append(gap_obj)

            duration_min = round(dt / 60.0, 1)
            severity = AnomalySeverity.NOTABLE if spanned else AnomalySeverity.INFO
            anomaly = BehavioralAnomaly(
                anomaly_type=BehavioralAnomalyType.AIS_TRANSMISSION_GAP,
                severity=severity,
                description=(
                    f"Observable AIS transmission gap of {duration_min} minutes ({round(dist_km, 2)} km) "
                    f"between consecutive received observations"
                ),
                timestamp=p1.timestamp,
                location_lon=p1.lon,
                location_lat=p1.lat,
                inside_source_zone=near_p1 or (polygon_ring and point_in_polygon(p1.lon, p1.lat, polygon_ring)),
                observed_value=round(dt, 1),
                baseline_or_threshold_value=gap_threshold_seconds,
                details={
                    "gap_duration_seconds": round(dt, 1),
                    "gap_duration_minutes": duration_min,
                    "distance_across_gap_km": round(dist_km, 3),
                    "gap_start_time": p1.timestamp.isoformat(),
                    "gap_end_time": p2.timestamp.isoformat(),
                    "spanned_source_zone": spanned,
                    "caveat": "Observable gap between received AIS transmissions only; no transponder disabling or vessel activity inferred.",
                },
            )
            anomalies.append(anomaly)

    return gaps, anomalies


def detect_speed_anomalies(
    positions: list[VesselPosition],
    polygon_ring: list[list[float]],
    center_lon: float,
    center_lat: float,
    radius_km: float,
    speed_drop_threshold_knots: float = 5.0,
) -> list[BehavioralAnomaly]:
    """Detect speed drops, speed surges, and SOG vs derived speed discrepancies."""
    anomalies: list[BehavioralAnomaly] = []
    if len(positions) < 2:
        return anomalies

    for i in range(len(positions) - 1):
        p1 = positions[i]
        p2 = positions[i + 1]

        v1 = p1.speed
        v2 = p2.speed
        dt = (p2.timestamp - p1.timestamp).total_seconds()
        dist_m = _haversine_m(p1.lon, p1.lat, p2.lon, p2.lat)

        derived_speed_knots: float | None = None
        if dt > 0.0:
            derived_speed_knots = (dist_m / dt) / 0.514444

        near_zone = (
            is_point_near_or_in_zone(p1.lon, p1.lat, polygon_ring, center_lon, center_lat, radius_km)
            or is_point_near_or_in_zone(p2.lon, p2.lat, polygon_ring, center_lon, center_lat, radius_km)
        )
        inside_zone = bool(polygon_ring and point_in_polygon(p2.lon, p2.lat, polygon_ring))

        # 1. Abrupt Speed Drop
        if v1 is not None and v2 is not None and near_zone:
            speed_drop = v1 - v2
            if speed_drop >= speed_drop_threshold_knots and v2 < 3.0:
                anomalies.append(
                    BehavioralAnomaly(
                        anomaly_type=BehavioralAnomalyType.SPEED_DROP_IN_ZONE,
                        severity=AnomalySeverity.NOTABLE,
                        description=(
                            f"Observed speed reduction of {round(speed_drop, 1)} kn "
                            f"(from {round(v1, 1)} kn to {round(v2, 1)} kn) near or inside source candidate zone"
                        ),
                        timestamp=p2.timestamp,
                        location_lon=p2.lon,
                        location_lat=p2.lat,
                        inside_source_zone=inside_zone,
                        observed_value=round(v2, 2),
                        baseline_or_threshold_value=round(v1, 2),
                        details={
                            "initial_speed_knots": round(v1, 2),
                            "reduced_speed_knots": round(v2, 2),
                            "speed_drop_knots": round(speed_drop, 2),
                        },
                    )
                )

            # 2. Abrupt Speed Surge / Rapid Departure
            speed_surge = v2 - v1
            if speed_surge >= 5.0 and v1 < 3.0 and v2 >= 8.0:
                anomalies.append(
                    BehavioralAnomaly(
                        anomaly_type=BehavioralAnomalyType.SPEED_SURGE_NEAR_ZONE,
                        severity=AnomalySeverity.NOTABLE,
                        description=(
                            f"Observed speed acceleration of {round(speed_surge, 1)} kn "
                            f"(from {round(v1, 1)} kn to {round(v2, 1)} kn) departing source candidate zone"
                        ),
                        timestamp=p2.timestamp,
                        location_lon=p2.lon,
                        location_lat=p2.lat,
                        inside_source_zone=inside_zone,
                        observed_value=round(v2, 2),
                        baseline_or_threshold_value=round(v1, 2),
                        details={
                            "initial_speed_knots": round(v1, 2),
                            "surged_speed_knots": round(v2, 2),
                            "speed_surge_knots": round(speed_surge, 2),
                        },
                    )
                )

        # 3. Reported SOG vs Derived Segment Speed Discrepancy
        if derived_speed_knots is not None and 10.0 <= dt <= 3600.0:
            avg_reported_sog: float | None = None
            if v1 is not None and v2 is not None:
                avg_reported_sog = 0.5 * (v1 + v2)
            elif v2 is not None:
                avg_reported_sog = v2
            elif v1 is not None:
                avg_reported_sog = v1

            if avg_reported_sog is not None:
                sog_diff = abs(avg_reported_sog - derived_speed_knots)
                if sog_diff >= 5.0:
                    anomalies.append(
                        BehavioralAnomaly(
                            anomaly_type=BehavioralAnomalyType.SOG_DERIVED_SPEED_DISCREPANCY,
                            severity=AnomalySeverity.INFO,
                            description=(
                                f"Observed discrepancy of {round(sog_diff, 1)} kn between reported SOG "
                                f"({round(avg_reported_sog, 1)} kn) and inter-observation derived speed ({round(derived_speed_knots, 1)} kn)"
                            ),
                            timestamp=p2.timestamp,
                            location_lon=p2.lon,
                            location_lat=p2.lat,
                            inside_source_zone=inside_zone,
                            observed_value=round(avg_reported_sog, 2),
                            baseline_or_threshold_value=round(derived_speed_knots, 2),
                            details={
                                "reported_sog_knots": round(avg_reported_sog, 2),
                                "derived_speed_knots": round(derived_speed_knots, 2),
                                "discrepancy_knots": round(sog_diff, 2),
                            },
                        )
                    )

    return anomalies


def detect_course_alterations(
    positions: list[VesselPosition],
    polygon_ring: list[list[float]],
    center_lon: float,
    center_lat: float,
    radius_km: float,
    course_threshold_deg: float = 45.0,
) -> list[BehavioralAnomaly]:
    """Detect sharp course alterations inside or near the source candidate zone."""
    anomalies: list[BehavioralAnomaly] = []
    if len(positions) < 2:
        return anomalies

    for i in range(len(positions) - 1):
        p1 = positions[i]
        p2 = positions[i + 1]

        c1 = p1.course
        c2 = p2.course

        if c1 is not None and c2 is not None:
            angle_diff = angular_difference_deg(c1, c2)
            near_zone = (
                is_point_near_or_in_zone(p1.lon, p1.lat, polygon_ring, center_lon, center_lat, radius_km)
                or is_point_near_or_in_zone(p2.lon, p2.lat, polygon_ring, center_lon, center_lat, radius_km)
            )
            inside_zone = bool(polygon_ring and point_in_polygon(p2.lon, p2.lat, polygon_ring))

            if angle_diff >= course_threshold_deg and near_zone:
                anomalies.append(
                    BehavioralAnomaly(
                        anomaly_type=BehavioralAnomalyType.COURSE_ALTERATION_IN_ZONE,
                        severity=AnomalySeverity.NOTABLE,
                        description=(
                            f"Observed course alteration of {round(angle_diff, 1)}° "
                            f"(from {round(c1, 1)}° to {round(c2, 1)}°) inside or near source candidate zone"
                        ),
                        timestamp=p2.timestamp,
                        location_lon=p2.lon,
                        location_lat=p2.lat,
                        inside_source_zone=inside_zone,
                        observed_value=round(angle_diff, 1),
                        baseline_or_threshold_value=course_threshold_deg,
                        details={
                            "initial_course_deg": round(c1, 1),
                            "new_course_deg": round(c2, 1),
                            "course_change_deg": round(angle_diff, 1),
                        },
                    )
                )

    return anomalies


def detect_loitering(
    positions: list[VesselPosition],
    polygon_ring: list[list[float]],
    center_lon: float,
    center_lat: float,
    radius_km: float,
    loitering_speed_threshold_knots: float = 3.0,
    is_anchored: bool = False,
) -> tuple[bool, float, list[BehavioralAnomaly]]:
    """Detect observed low-speed multi-course-change pattern in genuine observations.

    Constraint 2:
    - LOITERING_OBSERVED must mean only an observed low-speed, multi-course-change pattern
      in genuine AIS observations.
    - Never infer intent, suspiciousness, discharge activity, or culpability.
    """
    if is_anchored or len(positions) < 3:
        return False, 0.0, []

    # Find sequences of consecutive low-speed observations near or inside the zone
    low_speed_cluster: list[VesselPosition] = []
    max_duration_seconds = 0.0
    detected_cluster: list[VesselPosition] = []

    current_cluster: list[VesselPosition] = []
    for p in positions:
        near = is_point_near_or_in_zone(p.lon, p.lat, polygon_ring, center_lon, center_lat, radius_km)
        spd = p.speed if p.speed is not None else 0.0
        if near and spd <= loitering_speed_threshold_knots:
            current_cluster.append(p)
        else:
            if len(current_cluster) >= 3:
                dur = (current_cluster[-1].timestamp - current_cluster[0].timestamp).total_seconds()
                if dur > max_duration_seconds:
                    max_duration_seconds = dur
                    detected_cluster = list(current_cluster)
            current_cluster = []

    if len(current_cluster) >= 3:
        dur = (current_cluster[-1].timestamp - current_cluster[0].timestamp).total_seconds()
        if dur > max_duration_seconds:
            max_duration_seconds = dur
            detected_cluster = list(current_cluster)

    if len(detected_cluster) < 3 or max_duration_seconds < 600.0:
        return False, 0.0, []

    # Count significant course changes in cluster
    course_changes = 0
    for i in range(len(detected_cluster) - 1):
        c1 = detected_cluster[i].course
        c2 = detected_cluster[i + 1].course
        if c1 is not None and c2 is not None:
            if angular_difference_deg(c1, c2) >= 30.0:
                course_changes += 1

    if course_changes >= 2:
        mean_speed = sum(p.speed or 0.0 for p in detected_cluster) / len(detected_cluster)
        p_cpa = detected_cluster[len(detected_cluster) // 2]
        inside_zone = bool(polygon_ring and point_in_polygon(p_cpa.lon, p_cpa.lat, polygon_ring))

        anomaly = BehavioralAnomaly(
            anomaly_type=BehavioralAnomalyType.LOITERING_OBSERVED,
            severity=AnomalySeverity.NOTABLE,
            description=(
                f"Observed low-speed ({round(mean_speed, 1)} kn) multi-course-change pattern "
                f"over {round(max_duration_seconds / 60.0, 1)} minutes inside or near source candidate zone"
            ),
            timestamp=detected_cluster[0].timestamp,
            location_lon=p_cpa.lon,
            location_lat=p_cpa.lat,
            inside_source_zone=inside_zone,
            observed_value=round(mean_speed, 2),
            baseline_or_threshold_value=loitering_speed_threshold_knots,
            details={
                "observed_duration_seconds": round(max_duration_seconds, 1),
                "cluster_positions_count": len(detected_cluster),
                "mean_observed_speed_knots": round(mean_speed, 2),
                "course_alteration_count": course_changes,
                "caveat": "Descriptive kinematic pattern only; no operational intent, suspiciousness, or culpability inferred.",
            },
        )
        return True, round(max_duration_seconds, 1), [anomaly]

    return False, 0.0, []


def evaluate_nav_status_and_anchor(
    candidate: CandidateVessel,
    positions: list[VesselPosition],
) -> tuple[bool, AnchorSwingProfile | None, list[BehavioralAnomaly]]:
    """Evaluate navigation status consistency and compute anchor swing positional envelope.

    Constraint 3:
    - Anchor-swing analysis describes only the observed positional envelope/centroid
      from genuine AIS observations.
    - Does NOT claim an exact physical anchor position or swing circle.
    """
    anomalies: list[BehavioralAnomaly] = []
    nav_status_consistent = True
    anchor_profile: AnchorSwingProfile | None = None

    if not positions:
        return nav_status_consistent, anchor_profile, anomalies

    # Extract all reported statuses
    raw_status = candidate.navigation_status
    norm_status = normalize_nav_status(raw_status)

    speeds = [p.speed for p in positions if p.speed is not None]
    max_speed = max(speeds) if speeds else (candidate.speed_over_ground or 0.0)
    mean_speed = sum(speeds) / len(speeds) if speeds else (candidate.speed_over_ground or 0.0)

    is_anchored_or_moored = norm_status in ("at_anchor", "moored")
    is_stationary = mean_speed < 0.5 and max_speed < 1.0

    # 1. Nav status mismatch: reporting anchor/moored while moving at transit speed
    if is_anchored_or_moored and max_speed >= 3.0:
        nav_status_consistent = False
        p_latest = positions[-1]
        anomalies.append(
            BehavioralAnomaly(
                anomaly_type=BehavioralAnomalyType.NAV_STATUS_MISMATCH,
                severity=AnomalySeverity.NOTABLE,
                description=(
                    f"Vessel reported navigation status '{raw_status}' while observed moving at speeds up to {round(max_speed, 1)} kn"
                ),
                timestamp=p_latest.timestamp,
                location_lon=p_latest.lon,
                location_lat=p_latest.lat,
                inside_source_zone=candidate.inside_source_zone,
                observed_value=round(max_speed, 2),
                baseline_or_threshold_value=3.0,
                details={
                    "reported_navigation_status": raw_status,
                    "max_observed_speed_knots": round(max_speed, 2),
                    "mean_observed_speed_knots": round(mean_speed, 2),
                },
            )
        )

    # 2. Nav status mismatch: reporting 'under way' while stationary for extended time
    if norm_status == "under_way_engine" and is_stationary and len(positions) >= 3:
        duration_s = (positions[-1].timestamp - positions[0].timestamp).total_seconds()
        if duration_s >= 1800.0:
            nav_status_consistent = False
            p_latest = positions[-1]
            anomalies.append(
                BehavioralAnomaly(
                    anomaly_type=BehavioralAnomalyType.NAV_STATUS_MISMATCH,
                    severity=AnomalySeverity.INFO,
                    description=(
                        f"Vessel reported 'under way' but observed stationary (mean SOG {round(mean_speed, 2)} kn) "
                        f"over {round(duration_s / 60.0, 1)} minutes"
                    ),
                    timestamp=p_latest.timestamp,
                    location_lon=p_latest.lon,
                    location_lat=p_latest.lat,
                    inside_source_zone=candidate.inside_source_zone,
                    observed_value=round(mean_speed, 2),
                    baseline_or_threshold_value=0.5,
                    details={
                        "reported_navigation_status": raw_status,
                        "mean_observed_speed_knots": round(mean_speed, 2),
                        "observed_duration_seconds": round(duration_s, 1),
                    },
                )
            )

    # 3. Anchor Swing Positional Envelope
    if is_anchored_or_moored or (is_stationary and len(positions) >= 3):
        centroid_lon = sum(p.lon for p in positions) / len(positions)
        centroid_lat = sum(p.lat for p in positions) / len(positions)

        envelope_radius_m = max(
            _haversine_m(centroid_lon, centroid_lat, p.lon, p.lat) for p in positions
        )

        anchor_profile = AnchorSwingProfile(
            centroid_lon=round(centroid_lon, 6),
            centroid_lat=round(centroid_lat, 6),
            observed_envelope_radius_m=round(envelope_radius_m, 1),
            observation_count=len(positions),
            reported_nav_status=raw_status,
        )

        if len(positions) >= 2:
            anomalies.append(
                BehavioralAnomaly(
                    anomaly_type=BehavioralAnomalyType.ANCHOR_SWING_OBSERVED,
                    severity=AnomalySeverity.INFO,
                    description=(
                        f"Observed stationary/anchored positional envelope with radius {round(envelope_radius_m, 1)} m "
                        f"across {len(positions)} observations (centroid: {round(centroid_lat, 5)}°N, {round(centroid_lon, 5)}°E)"
                    ),
                    timestamp=positions[0].timestamp,
                    location_lon=round(centroid_lon, 6),
                    location_lat=round(centroid_lat, 6),
                    inside_source_zone=candidate.inside_source_zone,
                    observed_value=round(envelope_radius_m, 1),
                    baseline_or_threshold_value=None,
                    details={
                        "centroid_lon": round(centroid_lon, 6),
                        "centroid_lat": round(centroid_lat, 6),
                        "observed_envelope_radius_m": round(envelope_radius_m, 1),
                        "observation_count": len(positions),
                        "reported_status": raw_status,
                        "caveat": "Describes observed positional envelope/centroid only; does not claim an exact anchor drop point or chain length.",
                    },
                )
            )

    return nav_status_consistent, anchor_profile, anomalies


# ---------------------------------------------------------------------------
# GeoJSON Export & Registration
# ---------------------------------------------------------------------------

def export_behavioral_geojson(
    result: BehavioralIntelligenceResult,
    source_estimate: SourceEstimateResult,
    output_path: Path,
) -> None:
    """Export deterministic GeoJSON FeatureCollection of behavioural anomalies and transmission gaps."""
    features: list[dict[str, Any]] = []

    for prof in result.profiles:
        # Anomaly points
        for anom in prof.anomalies:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(anom.location_lon, 6), round(anom.location_lat, 6)],
                },
                "properties": {
                    "feature_kind": "behavioral_anomaly",
                    "candidate_id": prof.candidate_id,
                    "vessel_id": prof.vessel_id,
                    "mmsi": prof.mmsi,
                    "anomaly_type": anom.anomaly_type.value,
                    "severity": anom.severity.value,
                    "description": anom.description,
                    "timestamp": anom.timestamp.isoformat(),
                    "inside_source_zone": anom.inside_source_zone,
                    "observed_value": anom.observed_value,
                    "baseline_or_threshold_value": anom.baseline_or_threshold_value,
                    "details": anom.details,
                },
            })

        # Transmission gap chords (between observed endpoints only)
        for gap in prof.transmission_gaps:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [round(gap.gap_start_lon, 6), round(gap.gap_start_lat, 6)],
                        [round(gap.gap_end_lon, 6), round(gap.gap_end_lat, 6)],
                    ],
                },
                "properties": {
                    "feature_kind": "transmission_gap",
                    "candidate_id": prof.candidate_id,
                    "vessel_id": prof.vessel_id,
                    "mmsi": prof.mmsi,
                    "gap_start_time": gap.gap_start_time.isoformat(),
                    "gap_end_time": gap.gap_end_time.isoformat(),
                    "gap_duration_seconds": gap.gap_duration_seconds,
                    "distance_across_gap_km": gap.distance_across_gap_km,
                    "spanned_source_zone": gap.spanned_source_zone,
                    "zero_fabrication": True,
                    "caveat": "Observable gap between received AIS transmissions only; no movement or disabling inferred.",
                },
            })

    fc = {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "behavioral_intelligence_id": result.id,
            "investigation_id": result.investigation_id,
            "spill_detection_id": result.spill_detection_id,
            "source_estimate_id": result.source_estimate_id,
            "candidate_generation_id": result.candidate_generation_id,
            "trajectory_analysis_id": result.trajectory_analysis_id,
            "analyzed_vessel_count": result.analyzed_vessel_count,
            "total_anomalies_detected": result.total_anomalies_detected,
            "total_transmission_gaps_detected": result.total_transmission_gaps_detected,
            "zero_fabrication": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(fc, indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# Core Analysis Service
# ---------------------------------------------------------------------------

def analyze_candidate_behavior(
    investigation_id: str,
    spill_id: str,
    source_estimate: SourceEstimateResult,
    candidate_result: CandidateVesselGenerationResult,
    trajectory_analysis_id: str | None = None,
    speed_drop_threshold_knots: float = 5.0,
    loitering_speed_threshold_knots: float = 3.0,
    course_alteration_threshold_deg: float = 45.0,
    transmission_gap_threshold_seconds: float = 1800.0,
    output_dir: str | Path | None = None,
    registry: AssetRegistry | None = None,
) -> tuple[BehavioralIntelligenceResult, Asset]:
    """Execute Stage E3 Behavioural Intelligence analysis for candidate vessels.

    Returns:
        (BehavioralIntelligenceResult, Asset): Result object and registered DOCUMENT asset.
    """
    target_registry = registry or default_asset_registry

    # 1. Extract source zone polygon and center
    zone_geom = source_estimate.source_zone_geometry or {}
    zone_coords = zone_geom.get("coordinates", [])
    polygon_ring = zone_coords[0] if zone_coords and isinstance(zone_coords[0], list) else []
    center_lon = source_estimate.source_point_lon
    center_lat = source_estimate.source_point_lat
    radius_km = source_estimate.source_uncertainty_radius_km

    # 2. Analyze each candidate vessel
    profiles: list[VesselBehavioralProfile] = []
    total_anomalies = 0
    total_gaps = 0

    for cand in candidate_result.candidates:
        positions = sorted(cand.raw_positions, key=lambda p: p.timestamp)
        cand_anomalies: list[BehavioralAnomaly] = []
        summary_flags: list[str] = []

        # A. Transmission Gaps
        gaps, gap_anomalies = detect_transmission_gaps(
            positions=positions,
            polygon_ring=polygon_ring,
            center_lon=center_lon,
            center_lat=center_lat,
            radius_km=radius_km,
            gap_threshold_seconds=transmission_gap_threshold_seconds,
        )
        cand_anomalies.extend(gap_anomalies)
        if gaps:
            summary_flags.append(BehavioralAnomalyType.AIS_TRANSMISSION_GAP.value)
            total_gaps += len(gaps)

        # B. Nav Status Consistency & Anchor Swing
        norm_status = normalize_nav_status(cand.navigation_status)
        is_anchored = norm_status in ("at_anchor", "moored")
        consistent, anchor_profile, nav_anomalies = evaluate_nav_status_and_anchor(cand, positions)
        cand_anomalies.extend(nav_anomalies)
        for a in nav_anomalies:
            if a.anomaly_type.value not in summary_flags:
                summary_flags.append(a.anomaly_type.value)

        # C. Speed Anomalies
        speed_anoms = detect_speed_anomalies(
            positions=positions,
            polygon_ring=polygon_ring,
            center_lon=center_lon,
            center_lat=center_lat,
            radius_km=radius_km,
            speed_drop_threshold_knots=speed_drop_threshold_knots,
        )
        cand_anomalies.extend(speed_anoms)
        for a in speed_anoms:
            if a.anomaly_type.value not in summary_flags:
                summary_flags.append(a.anomaly_type.value)

        # D. Course Alterations
        course_anoms = detect_course_alterations(
            positions=positions,
            polygon_ring=polygon_ring,
            center_lon=center_lon,
            center_lat=center_lat,
            radius_km=radius_km,
            course_threshold_deg=course_alteration_threshold_deg,
        )
        cand_anomalies.extend(course_anoms)
        for a in course_anoms:
            if a.anomaly_type.value not in summary_flags:
                summary_flags.append(a.anomaly_type.value)

        # E. Loitering
        loit_detected, loit_dur, loit_anoms = detect_loitering(
            positions=positions,
            polygon_ring=polygon_ring,
            center_lon=center_lon,
            center_lat=center_lat,
            radius_km=radius_km,
            loitering_speed_threshold_knots=loitering_speed_threshold_knots,
            is_anchored=is_anchored,
        )
        cand_anomalies.extend(loit_anoms)
        if loit_detected and BehavioralAnomalyType.LOITERING_OBSERVED.value not in summary_flags:
            summary_flags.append(BehavioralAnomalyType.LOITERING_OBSERVED.value)

        total_anomalies += len(cand_anomalies)

        profile = VesselBehavioralProfile(
            candidate_id=cand.candidate_id,
            vessel_id=cand.vessel_id,
            mmsi=cand.mmsi,
            imo=cand.imo,
            vessel_name=cand.vessel_name,
            anomalies=cand_anomalies,
            transmission_gaps=gaps,
            loitering_detected=loit_detected,
            observed_loitering_duration_seconds=loit_dur,
            nav_status_consistent=consistent,
            anchor_swing_profile=anchor_profile,
            summary_flags=summary_flags,
            metadata={
                "observed_positions_count": len(positions),
                "inside_source_zone": cand.inside_source_zone,
            },
        )
        profiles.append(profile)

    # 3. Build BehavioralIntelligenceResult
    base_dir = Path(output_dir) if output_dir else Path(settings.data_dir)
    res_id = f"beh-intel-{_sanitize(spill_id, 'spill')}"

    result = BehavioralIntelligenceResult(
        id=res_id,
        investigation_id=investigation_id,
        spill_detection_id=spill_id,
        source_estimate_id=source_estimate.id,
        candidate_generation_id=candidate_result.id,
        trajectory_analysis_id=trajectory_analysis_id,
        analyzed_vessel_count=len(profiles),
        profiles=profiles,
        total_anomalies_detected=total_anomalies,
        total_transmission_gaps_detected=total_gaps,
        metadata={
            "speed_drop_threshold_knots": speed_drop_threshold_knots,
            "loitering_speed_threshold_knots": loitering_speed_threshold_knots,
            "course_alteration_threshold_deg": course_alteration_threshold_deg,
            "transmission_gap_threshold_seconds": transmission_gap_threshold_seconds,
            "zero_fabrication": True,
        },
    )

    # 4. Export GeoJSON artifact
    inv_dir = _sanitize(investigation_id, "inv")
    spill_clean = _sanitize(spill_id, "spill")
    artifact_path = base_dir / "derived" / inv_dir / "behavioral" / spill_clean / "candidate_behavioral_intelligence.geojson"

    export_behavioral_geojson(result, source_estimate, artifact_path)

    # 5. Register with AssetRegistry as AssetType.DOCUMENT
    # 5. Register with AssetRegistry as AssetType.DOCUMENT
    now = datetime.now(timezone.utc)
    parent_ids = [candidate_result.id, source_estimate.id]
    if trajectory_analysis_id:
        parent_ids.append(trajectory_analysis_id)

    prov = Provenance(
        provider_name="MARIS Stage E3 Behavioural Intelligence",
        source_uri=f"maris://investigation/{investigation_id}/spill/{spill_id}/behavioral-intelligence",
        acquired_at=now,
        retrieved_at=now,
        product_id=res_id,
        processing_level="behavioral_intelligence",
        parent_asset_ids=parent_ids,
        extra={
            "asset_type": "behavioral_intelligence",
            "stage": "E3",
            "spill_detection_id": spill_id,
            "source_estimate_id": source_estimate.id,
            "candidate_generation_id": candidate_result.id,
            "trajectory_analysis_id": trajectory_analysis_id,
            "analyzed_vessel_count": len(profiles),
            "total_anomalies_detected": total_anomalies,
            "total_transmission_gaps_detected": total_gaps,
            "zero_fabrication": True,
        },
    )

    artifact = AcquiredArtifact(
        asset_type=AssetType.DOCUMENT,
        location=str(artifact_path.resolve()),
        source="behavioral_intelligence_service",
        acquisition_time=now,
        provenance=prov,
        metadata={
            "asset_type": "behavioral_intelligence",
            "spill_id": spill_id,
            "investigation_id": investigation_id,
            "source_estimate_id": source_estimate.id,
            "candidate_generation_id": candidate_result.id,
            "trajectory_analysis_id": trajectory_analysis_id,
            "analyzed_vessel_count": len(profiles),
            "total_anomalies_detected": total_anomalies,
            "total_transmission_gaps_detected": total_gaps,
            "zero_fabrication": True,
        },
    )

    derived_asset = target_registry.register(
        investigation_id, "behavioral_intelligence_service", artifact
    )
    result.derived_asset_id = derived_asset.id

    return result, derived_asset

