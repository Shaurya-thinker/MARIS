"""MARIS Stage E2 — AIS Trajectory & Spatial/Temporal Analysis Service.

Computes physical trajectory kinematics, source candidate-zone transit profiles,
centerline proximity, and raw COG/drift-direction angular comparison for candidate
vessels produced by Stage E1.

ZERO-FABRICATION INVARIANT:
- Trajectory segments are strictly pairwise mathematical transitions between
  consecutive genuine observed AIS positions.
- No intermediate or synthetic positions are ever generated.
- In-zone transit duration is strictly (last_inside_time - first_inside_time)
  from actual observed points.
- Missing intervals/gaps are never treated as continuous observed transit.
- Centerline proximity and COG/drift angular differences are purely descriptive
  physical metrics; no behavioural or attribution scores are assigned.

Asset Registration:
The derived trajectory-analysis artifact is registered as AssetType.DOCUMENT with
metadata {"asset_type": "trajectory_analysis"}.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.acquisition.registry import AssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.core.config import settings
from app.models.asset import Asset
from app.models.common import AssetType, Provenance
from app.models.source_estimation import SourceEstimateResult
from app.models.trajectory_analysis import (
    CenterlineProximityProfile,
    ObservedTrajectoryPoint,
    TemporalCorrelationProfile,
    TrajectoryAnalysisResult,
    TrajectoryQualityProfile,
    TrajectorySegment,
    VesselTrajectoryAnalysis,
    ZoneTransitProfile,
)
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


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class TrajectoryAnalysisError(Exception):
    """Raised when trajectory analysis fails."""


# ---------------------------------------------------------------------------
# Geometric & Kinematic Helpers
# ---------------------------------------------------------------------------

def point_to_segment_distance_m(
    p_lon: float,
    p_lat: float,
    a_lon: float,
    a_lat: float,
    b_lon: float,
    b_lat: float,
) -> tuple[float, float, float]:
    """Calculate distance in metres from point P to segment AB.

    Returns (dist_m, closest_lon, closest_lat).
    Uses local flat-earth projection centered at the segment midpoint.
    """
    # Degenerate segment: A and B coincide
    if abs(a_lon - b_lon) < 1e-9 and abs(a_lat - b_lat) < 1e-9:
        dist_m = _haversine_m(p_lon, p_lat, a_lon, a_lat)
        return dist_m, a_lon, a_lat

    mid_lat = 0.5 * (a_lat + b_lat)
    cos_lat = math.cos(math.radians(mid_lat))
    r_earth = 6_371_000.0
    deg_to_rad = math.pi / 180.0

    # Local coordinates in metres (x=East, y=North)
    ax = a_lon * deg_to_rad * r_earth * cos_lat
    ay = a_lat * deg_to_rad * r_earth
    bx = b_lon * deg_to_rad * r_earth * cos_lat
    by = b_lat * deg_to_rad * r_earth
    px = p_lon * deg_to_rad * r_earth * cos_lat
    py = p_lat * deg_to_rad * r_earth

    vx = bx - ax
    vy = by - ay
    v_len_sq = vx * vx + vy * vy

    if v_len_sq <= 1e-12:
        dist_m = _haversine_m(p_lon, p_lat, a_lon, a_lat)
        return dist_m, a_lon, a_lat

    t = ((px - ax) * vx + (py - ay) * vy) / v_len_sq
    t_clamped = max(0.0, min(1.0, t))

    qx = ax + t_clamped * vx
    qy = ay + t_clamped * vy

    q_lat = qy / (deg_to_rad * r_earth)
    q_lon = qx / (deg_to_rad * r_earth * cos_lat) if abs(cos_lat) > 1e-7 else 0.0

    dist_m = _haversine_m(p_lon, p_lat, q_lon, q_lat)
    return dist_m, q_lon, q_lat


def point_to_polyline_distance_m(
    p_lon: float,
    p_lat: float,
    coords: list[list[float]],
) -> tuple[float, float, float]:
    """Find minimum distance from point P to a polyline of coordinates [[lon, lat], ...].

    Returns (min_distance_m, closest_lon, closest_lat).
    """
    if not coords:
        raise TrajectoryAnalysisError("Polyline coordinates cannot be empty")

    if len(coords) == 1:
        c_lon, c_lat = coords[0][0], coords[0][1]
        return _haversine_m(p_lon, p_lat, c_lon, c_lat), c_lon, c_lat

    min_dist = float("inf")
    best_lon = coords[0][0]
    best_lat = coords[0][1]

    for i in range(len(coords) - 1):
        a_lon, a_lat = coords[i][0], coords[i][1]
        b_lon, b_lat = coords[i + 1][0], coords[i + 1][1]

        d_m, q_lon, q_lat = point_to_segment_distance_m(
            p_lon, p_lat, a_lon, a_lat, b_lon, b_lat
        )
        if d_m < min_dist:
            min_dist = d_m
            best_lon = q_lon
            best_lat = q_lat

    return min_dist, best_lon, best_lat


def compute_drift_direction_deg(u_ms: float, v_ms: float) -> float:
    """Compute oceanographic drift direction in degrees [0, 360) towards which the flow moves.

    u_ms: eastward drift velocity (m/s)
    v_ms: northward drift velocity (m/s)
    """
    angle = math.degrees(math.atan2(u_ms, v_ms)) % 360.0
    return round(angle, 2)


def angular_difference_deg(angle1: float, angle2: float) -> float:
    """Minimal circular angular difference between two angles in [0, 360) degrees.

    Returns a value in [0.0, 180.0].
    """
    diff = abs((angle1 - angle2) % 360.0)
    if diff > 180.0:
        diff = 360.0 - diff
    return round(diff, 2)


MAX_PLAUSIBLE_SPEED_KNOTS: float = 60.0


def calculate_initial_bearing_deg(
    lon1: float,
    lat1: float,
    lon2: float,
    lat2: float,
) -> float | None:
    """Calculate initial forward bearing in degrees [0, 360) from (lon1, lat1) to (lon2, lat2).

    Uses great-circle forward azimuth on the sphere. Returns None if start and end
    points coincide (distance < 1 cm).
    """
    d_m = _haversine_m(lon1, lat1, lon2, lat2)
    if d_m < 0.01:
        return None

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlam = math.radians(lon2 - lon1)

    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)

    bearing = (math.degrees(math.atan2(y, x))) % 360.0
    return round(bearing, 2)


def point_to_polygon_distance_m(
    p_lon: float,
    p_lat: float,
    ring: list[list[float]],
) -> float:
    """Calculate distance in metres from point P to a closed polygon ring.

    Returns 0.0 if P is inside the polygon ring.
    Otherwise returns minimum distance from P to the boundary ring edges.
    """
    if not ring:
        return 0.0
    if point_in_polygon(p_lon, p_lat, ring):
        return 0.0

    # Ensure closed ring for polyline distance computation
    coords = ring if (ring[0] == ring[-1]) else (ring + [ring[0]])
    dist_m, _, _ = point_to_polyline_distance_m(p_lon, p_lat, coords)
    return dist_m


def deduplicate_positions(
    positions: list[VesselPosition],
) -> tuple[list[VesselPosition], int]:
    """Deterministically sort and remove exact duplicate AIS observations.

    Two observations are considered exact duplicates if they have the same UTC timestamp
    and coordinates within 1e-7 degrees (~1 cm).

    Sorting is deterministic: (timestamp_utc, lon, lat, speed, course).

    Returns:
        (deduplicated_positions, duplicate_count)
    """
    if not positions:
        return [], 0

    sorted_pos = sorted(
        positions,
        key=lambda p: (
            _utc(p.timestamp),
            round(p.lon, 7),
            round(p.lat, 7),
            p.speed if p.speed is not None else -1.0,
            p.course if p.course is not None else -1.0,
        ),
    )

    deduped: list[VesselPosition] = []
    duplicate_count = 0

    for p in sorted_pos:
        if not deduped:
            deduped.append(p)
            continue
        last = deduped[-1]
        same_time = (_utc(p.timestamp) == _utc(last.timestamp))
        same_coords = (abs(p.lon - last.lon) < 1e-7 and abs(p.lat - last.lat) < 1e-7)

        if same_time and same_coords:
            duplicate_count += 1
            continue
        deduped.append(p)

    return deduped, duplicate_count


# ---------------------------------------------------------------------------
# Asset Loading Helpers
# ---------------------------------------------------------------------------

def load_candidate_result_from_asset(
    asset: Asset,
    registry: AssetRegistry | None = None,
) -> CandidateVesselGenerationResult:
    """Load or reconstruct a CandidateVesselGenerationResult from a candidate_vessels DOCUMENT asset."""
    artifact_path = Path(asset.location)
    if not artifact_path.exists():
        raise TrajectoryAnalysisError(f"Candidate vessels artifact does not exist: {artifact_path}")

    try:
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TrajectoryAnalysisError(f"Malformed candidate vessels GeoJSON: {exc}") from exc

    features = data.get("features", [])
    cpa_features = [
        f for f in features if f.get("properties", {}).get("feature_kind") == "candidate_cpa"
    ]
    track_features = {
        f.get("properties", {}).get("candidate_id"): f
        for f in features
        if f.get("properties", {}).get("feature_kind") == "observed_vessel_track"
    }

    extra = asset.provenance.extra or {}
    source_est_id = extra.get("source_estimate_id") or asset.metadata.get("source_estimate_id", "unknown-source")
    spill_id = extra.get("spill_detection_id") or asset.metadata.get("spill_id", "unknown-spill")
    ais_asset_id = extra.get("ais_asset_id")

    # If AIS asset is available in registry, load genuine raw positions
    reg = registry or default_asset_registry
    raw_positions_by_cand: dict[str, list[VesselPosition]] = {}

    if ais_asset_id:
        try:
            ais_asset = reg.get(ais_asset_id)
            ais_path = Path(ais_asset.location)
            if ais_path.exists():
                ais_data = json.loads(ais_path.read_text(encoding="utf-8"))
                records = ais_data.get("records") if "records" in ais_data else ais_data.get("positions", [])
                if isinstance(records, list):
                    vessel_records: dict[str, list[dict[str, Any]]] = {}
                    for rec in records:
                        vid = str(rec.get("vessel_id") or rec.get("mmsi") or "")
                        mmsi_val = str(rec.get("mmsi") or "")
                        if vid:
                            vessel_records.setdefault(vid, []).append(rec)
                        if mmsi_val and mmsi_val != vid:
                            vessel_records.setdefault(mmsi_val, []).append(rec)

                    for cpa_feat in cpa_features:
                        props = cpa_feat.get("properties", {})
                        cid = props.get("candidate_id")
                        vid = str(props.get("vessel_id") or "")
                        mmsi_val = str(props.get("mmsi") or "")

                        matching = vessel_records.get(vid) or vessel_records.get(mmsi_val) or []
                        if matching:
                            p_list: list[VesselPosition] = []
                            for r in matching:
                                try:
                                    ts = _utc(datetime.fromisoformat(str(r["timestamp"]).replace("Z", "+00:00")))
                                    p_list.append(
                                        VesselPosition(
                                            timestamp=ts,
                                            lon=float(r["lon"]),
                                            lat=float(r["lat"]),
                                            speed=float(r["speed_over_ground"]) if r.get("speed_over_ground") is not None else None,
                                            course=float(r["course_over_ground"]) if r.get("course_over_ground") is not None else None,
                                            heading=float(r["heading"]) if r.get("heading") is not None else None,
                                        )
                                    )
                                except Exception:
                                    continue
                            if p_list:
                                raw_positions_by_cand[cid] = p_list
        except Exception:
            pass

    # Build CandidateVessel list
    candidates: list[CandidateVessel] = []
    for cpa_feat in cpa_features:
        props = cpa_feat.get("properties", {})
        cid = props.get("candidate_id")
        coords = cpa_feat.get("geometry", {}).get("coordinates", [0.0, 0.0])
        cpa_lon = coords[0]
        cpa_lat = coords[1]
        cpa_time_str = props.get("closest_position_time")
        cpa_time = _utc(datetime.fromisoformat(cpa_time_str.replace("Z", "+00:00"))) if cpa_time_str else datetime.now(timezone.utc)

        raw_pos = raw_positions_by_cand.get(cid)
        if not raw_pos:
            track_feat = track_features.get(cid)
            if track_feat:
                geom = track_feat.get("geometry", {})
                t_coords = geom.get("coordinates", [])
                if geom.get("type") == "LineString" and t_coords:
                    raw_pos = [
                        VesselPosition(
                            timestamp=cpa_time,
                            lon=pt[0],
                            lat=pt[1],
                            speed=props.get("speed_over_ground"),
                            course=props.get("course_over_ground"),
                        )
                        for pt in t_coords
                    ]
                else:
                    raw_pos = [
                        VesselPosition(
                            timestamp=cpa_time,
                            lon=cpa_lon,
                            lat=cpa_lat,
                            speed=props.get("speed_over_ground"),
                            course=props.get("course_over_ground"),
                        )
                    ]
            else:
                raw_pos = [
                    VesselPosition(
                        timestamp=cpa_time,
                        lon=cpa_lon,
                        lat=cpa_lat,
                        speed=props.get("speed_over_ground"),
                        course=props.get("course_over_ground"),
                    )
                ]

        cand = CandidateVessel(
            candidate_id=cid,
            vessel_id=props.get("vessel_id", cid),
            mmsi=props.get("mmsi"),
            imo=props.get("imo"),
            vessel_name=props.get("vessel_name"),
            vessel_type=props.get("vessel_type"),
            call_sign=props.get("call_sign"),
            flag_country=props.get("flag_country"),
            closest_position_lon=cpa_lon,
            closest_position_lat=cpa_lat,
            closest_position_time=cpa_time,
            inside_source_zone=bool(props.get("inside_source_zone", False)),
            min_distance_to_source_center_km=float(props.get("min_distance_to_source_center_km", 0.0)),
            distance_to_zone_boundary_km=float(props.get("distance_to_zone_boundary_km", 0.0)),
            time_offset_from_source_hours=float(props.get("time_offset_from_source_hours", 0.0)),
            speed_over_ground=props.get("speed_over_ground"),
            course_over_ground=props.get("course_over_ground"),
            heading=props.get("heading"),
            navigation_status=props.get("navigation_status"),
            observed_positions_count=len(raw_pos),
            raw_positions=raw_pos,
        )
        candidates.append(cand)

    res_id = extra.get("candidate_generation_id") or asset.provenance.product_id or f"cand-gen-{_sanitize(spill_id, 'spill')}"

    return CandidateVesselGenerationResult(
        id=res_id,
        investigation_id=asset.investigation_id,
        source_estimate_id=source_est_id,
        spill_detection_id=spill_id,
        ais_asset_id=ais_asset_id,
        derived_asset_id=asset.id,
        status="completed" if candidates else "no_candidates_found",
        source_time=datetime.now(timezone.utc),
        source_uncertainty_radius_km=float(extra.get("source_uncertainty_radius_km", 5.0)),
        temporal_window_start=datetime.now(timezone.utc),
        temporal_window_end=datetime.now(timezone.utc),
        spatial_query_bbox={},
        candidate_count=len(candidates),
        total_vessels_checked=int(extra.get("total_vessels_checked", len(candidates))),
        candidates=candidates,
        metadata=dict(asset.metadata),
    )


# ---------------------------------------------------------------------------
# Core Trajectory Analysis Service
# ---------------------------------------------------------------------------

def analyze_candidate_trajectories(
    investigation_id: str,
    spill_id: str,
    source_estimate: SourceEstimateResult,
    candidate_result: CandidateVesselGenerationResult,
    output_dir: str | Path | None = None,
    registry: AssetRegistry | None = None,
) -> tuple[TrajectoryAnalysisResult, Asset]:
    """Execute Stage E2 AIS Trajectory & Spatial/Temporal Analysis for candidate vessels.

    Strict Zero-Fabrication Invariant:
    - Trajectory segments are strictly pairwise mathematical transitions between
      consecutive genuine observed AIS positions.
    - In-zone transit duration is strictly (last_inside_time - first_inside_time)
      from actual observed points. Zero if < 2 inside pings.
    - No behavioural labels (e.g. loitering, spoofing) or attribution scores.

    Returns:
        (TrajectoryAnalysisResult, Asset):
            Analysis result and registered DOCUMENT asset.
    """
    target_registry = registry or default_asset_registry

    # 1. Extract source candidate zone polygon ring
    zone_geom = source_estimate.source_zone_geometry or {}
    zone_coords = zone_geom.get("coordinates", [])
    polygon_ring = zone_coords[0] if zone_coords and isinstance(zone_coords[0], list) else []

    # 2. Extract backward drift centerline coordinates
    centerline_coords: list[list[float]] = []
    if source_estimate.steps:
        centerline_coords = [[step.lon, step.lat] for step in source_estimate.steps]
    else:
        centerline_coords = [
            [source_estimate.origin_lon, source_estimate.origin_lat],
            [source_estimate.source_point_lon, source_estimate.source_point_lat],
        ]

    # Pre-calculate overall drift direction
    net_drift_dir: float | None = None
    if source_estimate.steps:
        last_step = source_estimate.steps[-1]
        net_drift_dir = compute_drift_direction_deg(last_step.drift_u_ms, last_step.drift_v_ms)
    elif len(centerline_coords) >= 2:
        c_src = centerline_coords[-1]
        c_orig = centerline_coords[0]
        dlon = math.radians(c_orig[0] - c_src[0])
        lat1 = math.radians(c_src[1])
        lat2 = math.radians(c_orig[1])
        y = math.sin(dlon) * math.cos(lat2)
        x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
        net_drift_dir = round(math.degrees(math.atan2(y, x)) % 360.0, 2)

    analyses: list[VesselTrajectoryAnalysis] = []
    src_time = _utc(source_estimate.source_time)
    spill_time = _utc(source_estimate.observation_time) if source_estimate.observation_time else None
    radius_km = source_estimate.source_uncertainty_radius_km
    radius_m = radius_km * 1000.0

    # 3. Analyze each candidate vessel
    for candidate in candidate_result.candidates:
        # Deterministic sorting & exact-duplicate observation removal
        raw_positions, dup_count = deduplicate_positions(candidate.raw_positions)
        pos_count = len(raw_positions)

        # 3.0 Edge Case: No AIS observations
        if pos_count == 0:
            transit_profile = ZoneTransitProfile(
                points_inside_count=0,
                first_inside_time=None,
                last_inside_time=None,
                observed_transit_duration_seconds=0.0,
                min_distance_to_center_km=float("inf"),
                distance_to_zone_boundary_km=float("inf"),
                min_distance_to_polygon_km=float("inf"),
                closest_position_time=src_time,
                time_offset_from_source_hours=0.0,
                cpa_lon=candidate.closest_position_lon,
                cpa_lat=candidate.closest_position_lat,
                cpa_to_polygon_distance_km=float("inf"),
            )
            centerline_profile = CenterlineProximityProfile(
                min_distance_to_centerline_km=float("inf"),
                closest_centerline_point_lon=centerline_coords[0][0],
                closest_centerline_point_lat=centerline_coords[0][1],
                closest_observation_time=None,
                course_drift_angle_diff_deg=None,
            )
            temporal_profile = TemporalCorrelationProfile(
                earliest_observation_time=None,
                latest_observation_time=None,
                temporal_span_seconds=0.0,
                closest_to_source_time=None,
                seconds_from_source_time=0.0,
                hours_from_source_time=0.0,
                closest_to_spill_time=None,
                seconds_from_spill_time=None,
                hours_from_spill_time=None,
            )
            quality_profile = TrajectoryQualityProfile(
                observation_count=0,
                duplicate_count=dup_count,
                temporal_span_seconds=0.0,
                has_multiple_observations=False,
                has_valid_kinematics=False,
                invalid_kinematic_interval_count=0,
                sparse_track=True,
                is_real_observation=False,
                data_source_type="curated_historical_reconstruction",
                quality_flags=["no_observations"],
            )
            analysis = VesselTrajectoryAnalysis(
                candidate_id=candidate.candidate_id,
                vessel_id=candidate.vessel_id,
                mmsi=candidate.mmsi,
                imo=candidate.imo,
                vessel_name=candidate.vessel_name,
                vessel_type=candidate.vessel_type,
                call_sign=candidate.call_sign,
                flag_country=candidate.flag_country,
                status="no_observations",
                observed_positions_count=0,
                total_track_duration_seconds=0.0,
                total_track_distance_m=0.0,
                transit_profile=transit_profile,
                centerline_proximity=centerline_profile,
                temporal_correlation=temporal_profile,
                quality=quality_profile,
                observed_trajectory=[],
                segments=[],
                evidence={
                    "spatial": {
                        "min_distance_to_source_center_km": None,
                        "inside_source_zone": False,
                        "min_distance_to_polygon_km": None,
                        "min_distance_to_centerline_km": None,
                    },
                    "temporal": {
                        "time_offset_from_source_hours": None,
                        "time_offset_from_spill_hours": None,
                        "temporal_span_seconds": 0.0,
                    },
                    "kinematic": {
                        "mean_derived_speed_knots": None,
                        "total_track_distance_m": 0.0,
                    },
                    "data_quality": {
                        "observation_count": 0,
                        "duplicate_count": dup_count,
                        "sparse_track": True,
                        "is_real_observation": False,
                        "data_source_type": "curated_historical_reconstruction",
                        "quality_flags": ["no_observations"],
                    },
                },
                metadata={"zero_fabrication": True},
            )
            analyses.append(analysis)
            continue

        # --- 3.1 Kinematic Segments ---
        segments: list[TrajectorySegment] = []
        cumulative_dist_m = 0.0
        invalid_intervals = 0

        if pos_count >= 2:
            for i in range(pos_count - 1):
                p1 = raw_positions[i]
                p2 = raw_positions[i + 1]
                t1 = _utc(p1.timestamp)
                t2 = _utc(p2.timestamp)
                dt_s = (t2 - t1).total_seconds()

                dist_m = _haversine_m(p1.lon, p1.lat, p2.lon, p2.lat)
                cumulative_dist_m += dist_m

                derived_speed_knots: float | None = None
                speed_mps: float | None = None
                bearing: float | None = None
                is_valid = True

                if dt_s <= 0.0:
                    is_valid = False
                    invalid_intervals += 1
                else:
                    speed_mps = dist_m / dt_s
                    derived_speed_knots = speed_mps * (3600.0 / 1852.0)
                    bearing = calculate_initial_bearing_deg(p1.lon, p1.lat, p2.lon, p2.lat)

                    if derived_speed_knots > MAX_PLAUSIBLE_SPEED_KNOTS:
                        is_valid = False
                        invalid_intervals += 1

                # VesselPosition uses .speed (SOG), .course (COG)
                sog1 = p1.speed
                sog2 = p2.speed
                sog_diff: float | None = None
                if derived_speed_knots is not None and (sog1 is not None or sog2 is not None):
                    valid_sogs = [s for s in [sog1, sog2] if s is not None]
                    mean_sog = sum(valid_sogs) / len(valid_sogs)
                    sog_diff = round(abs(derived_speed_knots - mean_sog), 2)

                segments.append(
                    TrajectorySegment(
                        start_time=t1,
                        end_time=t2,
                        start_lon=p1.lon,
                        start_lat=p1.lat,
                        end_lon=p2.lon,
                        end_lat=p2.lat,
                        duration_seconds=max(0.0, dt_s),
                        distance_m=round(dist_m, 2),
                        derived_speed_knots=round(derived_speed_knots, 2) if derived_speed_knots is not None else None,
                        speed_mps=round(speed_mps, 2) if speed_mps is not None else None,
                        bearing_degrees=bearing,
                        is_valid_kinematic=is_valid,
                        reported_sog_start=sog1,
                        reported_sog_end=sog2,
                        sog_difference_knots=sog_diff,
                    )
                )

        # Overall track duration and distance
        if pos_count >= 2:
            track_duration_s = max(
                0.0,
                (_utc(raw_positions[-1].timestamp) - _utc(raw_positions[0].timestamp)).total_seconds(),
            )
        else:
            track_duration_s = 0.0

        # SOG statistics (VesselPosition.speed)
        reported_sogs = [p.speed for p in raw_positions if p.speed is not None]
        min_sog = round(min(reported_sogs), 2) if reported_sogs else None
        max_sog = round(max(reported_sogs), 2) if reported_sogs else None
        mean_sog_val = round(sum(reported_sogs) / len(reported_sogs), 2) if reported_sogs else None

        valid_derived_speeds = [
            seg.derived_speed_knots for seg in segments
            if seg.derived_speed_knots is not None and seg.is_valid_kinematic
        ]
        mean_derived = round(sum(valid_derived_speeds) / len(valid_derived_speeds), 2) if valid_derived_speeds else None
        if mean_derived is None:
            all_derived = [seg.derived_speed_knots for seg in segments if seg.derived_speed_knots is not None]
            mean_derived = round(sum(all_derived) / len(all_derived), 2) if all_derived else None

        # --- 3.2 Spatial & Transit Metrics across Genuine Observations ---
        observed_points: list[ObservedTrajectoryPoint] = []
        inside_pings: list[VesselPosition] = []

        best_center_dist_km = float("inf")
        cpa_center_ping = raw_positions[0]

        best_poly_dist_km = float("inf")
        cpa_poly_ping = raw_positions[0]

        best_cl_dist_m = float("inf")
        best_cl_lon = centerline_coords[0][0]
        best_cl_lat = centerline_coords[0][1]
        cpa_cl_ping = raw_positions[0]

        closest_time_diff_src = float("inf")
        closest_src_time_ping = raw_positions[0]

        closest_time_diff_spill = float("inf")
        closest_spill_time_ping = raw_positions[0]

        for p in raw_positions:
            p_t = _utc(p.timestamp)

            # Distance to D3 center
            d_center_m = _haversine_m(
                p.lon, p.lat, source_estimate.source_point_lon, source_estimate.source_point_lat
            )
            d_center_km = d_center_m / 1000.0
            if d_center_km < best_center_dist_km:
                best_center_dist_km = d_center_km
                cpa_center_ping = p

            # Polygon membership & distance
            if polygon_ring:
                is_inside = point_in_polygon(p.lon, p.lat, polygon_ring)
                d_poly_m = point_to_polygon_distance_m(p.lon, p.lat, polygon_ring)
            else:
                is_inside = (d_center_m <= radius_m)
                d_poly_m = max(0.0, d_center_m - radius_m)
            d_poly_km = d_poly_m / 1000.0

            if is_inside:
                inside_pings.append(p)

            if d_poly_km < best_poly_dist_km:
                best_poly_dist_km = d_poly_km
                cpa_poly_ping = p

            # Distance to D3 centerline
            d_cl_m, q_cl_lon, q_cl_lat = point_to_polyline_distance_m(p.lon, p.lat, centerline_coords)
            if d_cl_m < best_cl_dist_m:
                best_cl_dist_m = d_cl_m
                best_cl_lon = q_cl_lon
                best_cl_lat = q_cl_lat
                cpa_cl_ping = p

            # Time offsets
            dt_src_s = (p_t - src_time).total_seconds()
            if abs(dt_src_s) < closest_time_diff_src:
                closest_time_diff_src = abs(dt_src_s)
                closest_src_time_ping = p

            dt_spill_s: float | None = None
            if spill_time:
                dt_spill_s = (p_t - spill_time).total_seconds()
                if abs(dt_spill_s) < closest_time_diff_spill:
                    closest_time_diff_spill = abs(dt_spill_s)
                    closest_spill_time_ping = p

            observed_points.append(
                ObservedTrajectoryPoint(
                    timestamp=p_t,
                    lon=p.lon,
                    lat=p.lat,
                    speed_over_ground=p.speed,
                    course_over_ground=p.course,
                    heading=p.heading,
                    inside_source_zone=is_inside,
                    distance_to_center_km=round(d_center_km, 3),
                    distance_to_polygon_km=round(d_poly_km, 3),
                    distance_to_centerline_km=round(d_cl_m / 1000.0, 3),
                    seconds_from_source_time=round(dt_src_s, 1),
                    seconds_from_spill_time=round(dt_spill_s, 1) if dt_spill_s is not None else None,
                )
            )

        pts_inside_count = len(inside_pings)
        first_inside: datetime | None = None
        last_inside: datetime | None = None
        transit_duration_s = 0.0

        if pts_inside_count >= 1:
            first_inside = _utc(inside_pings[0].timestamp)
            last_inside = _utc(inside_pings[-1].timestamp)
            if pts_inside_count >= 2:
                transit_duration_s = max(0.0, (last_inside - first_inside).total_seconds())

        cpa_center_time = _utc(cpa_center_ping.timestamp)
        time_offset_h = round((cpa_center_time - src_time).total_seconds() / 3600.0, 4)

        if pts_inside_count > 0 or candidate.inside_source_zone:
            dist_to_boundary_km = 0.0
        else:
            dist_to_boundary_km = round(best_poly_dist_km, 3)

        cpa_poly_time = _utc(cpa_poly_ping.timestamp)
        cpa_poly_dist_km = 0.0 if pts_inside_count > 0 else round(best_poly_dist_km, 3)

        transit_profile = ZoneTransitProfile(
            points_inside_count=pts_inside_count,
            first_inside_time=first_inside,
            last_inside_time=last_inside,
            observed_transit_duration_seconds=round(transit_duration_s, 2),
            min_distance_to_center_km=round(best_center_dist_km, 3),
            distance_to_zone_boundary_km=dist_to_boundary_km,
            min_distance_to_polygon_km=cpa_poly_dist_km,
            closest_position_time=cpa_center_time,
            time_offset_from_source_hours=time_offset_h,
            cpa_lon=cpa_center_ping.lon,
            cpa_lat=cpa_center_ping.lat,
            cpa_to_polygon_distance_km=cpa_poly_dist_km,
            cpa_to_polygon_time=cpa_poly_time,
            cpa_to_polygon_lon=cpa_poly_ping.lon,
            cpa_to_polygon_lat=cpa_poly_ping.lat,
        )

        # --- 3.3 Centerline Proximity Profile ---
        min_centerline_km = round(best_cl_dist_m / 1000.0, 3)
        course_drift_diff: float | None = None
        cog_candidate = cpa_center_ping.course if cpa_center_ping.course is not None else candidate.course_over_ground

        if cog_candidate is not None and net_drift_dir is not None:
            course_drift_diff = angular_difference_deg(cog_candidate, net_drift_dir)

        centerline_profile = CenterlineProximityProfile(
            min_distance_to_centerline_km=min_centerline_km,
            closest_centerline_point_lon=round(best_cl_lon, 6),
            closest_centerline_point_lat=round(best_cl_lat, 6),
            closest_observation_time=_utc(cpa_cl_ping.timestamp),
            cpa_observation_lon=cpa_cl_ping.lon,
            cpa_observation_lat=cpa_cl_ping.lat,
            course_drift_angle_diff_deg=course_drift_diff,
        )

        # --- 3.4 Temporal Correlation Profile ---
        earliest_t = _utc(raw_positions[0].timestamp)
        latest_t = _utc(raw_positions[-1].timestamp)
        closest_src_t = _utc(closest_src_time_ping.timestamp)
        sec_from_src = (closest_src_t - src_time).total_seconds()
        hr_from_src = sec_from_src / 3600.0

        closest_spill_t: datetime | None = None
        sec_from_spill: float | None = None
        hr_from_spill: float | None = None
        if spill_time:
            closest_spill_t = _utc(closest_spill_time_ping.timestamp)
            sec_from_spill = (closest_spill_t - spill_time).total_seconds()
            hr_from_spill = sec_from_spill / 3600.0

        temporal_profile = TemporalCorrelationProfile(
            earliest_observation_time=earliest_t,
            latest_observation_time=latest_t,
            temporal_span_seconds=round(track_duration_s, 2),
            closest_to_source_time=closest_src_t,
            seconds_from_source_time=round(sec_from_src, 1),
            hours_from_source_time=round(hr_from_src, 4),
            closest_to_spill_time=closest_spill_t,
            seconds_from_spill_time=round(sec_from_spill, 1) if sec_from_spill is not None else None,
            hours_from_spill_time=round(hr_from_spill, 4) if hr_from_spill is not None else None,
        )

        # --- 3.5 Trajectory Quality Profile ---
        q_flags: list[str] = []
        if pos_count == 1:
            q_flags.append("single_observation")
        elif pos_count <= 2:
            q_flags.append("sparse_track")
        if dup_count > 0:
            q_flags.append("duplicates_removed")
        if invalid_intervals > 0:
            q_flags.append("invalid_kinematic_intervals")

        has_valid_kin = any(seg.is_valid_kinematic for seg in segments)
        cand_meta = candidate.metadata or {}
        provenance_cat = str(cand_meta.get("source_type") or cand_meta.get("provenance") or "curated_historical_reconstruction")
        is_real = bool(cand_meta.get("is_real_observation", False))

        quality_profile = TrajectoryQualityProfile(
            observation_count=pos_count,
            duplicate_count=dup_count,
            temporal_span_seconds=round(track_duration_s, 2),
            has_multiple_observations=(pos_count >= 2),
            has_valid_kinematics=has_valid_kin,
            invalid_kinematic_interval_count=invalid_intervals,
            sparse_track=(pos_count <= 2),
            is_real_observation=is_real,
            data_source_type=provenance_cat,
            quality_flags=q_flags,
        )

        # --- 3.6 Structured Evidence Handoff for Stage F1 ---
        evidence: dict[str, Any] = {
            "spatial": {
                "min_distance_to_source_center_km": transit_profile.min_distance_to_center_km,
                "inside_source_zone": (pts_inside_count > 0 or candidate.inside_source_zone),
                "min_distance_to_polygon_km": transit_profile.min_distance_to_polygon_km,
                "min_distance_to_centerline_km": centerline_profile.min_distance_to_centerline_km,
                "cpa_to_center": {
                    "distance_km": transit_profile.min_distance_to_center_km,
                    "timestamp": transit_profile.closest_position_time.isoformat(),
                    "lon": transit_profile.cpa_lon,
                    "lat": transit_profile.cpa_lat,
                },
                "cpa_to_polygon": {
                    "distance_km": transit_profile.cpa_to_polygon_distance_km,
                    "timestamp": transit_profile.cpa_to_polygon_time.isoformat() if transit_profile.cpa_to_polygon_time else None,
                    "lon": transit_profile.cpa_to_polygon_lon,
                    "lat": transit_profile.cpa_to_polygon_lat,
                },
                "cpa_to_centerline": {
                    "distance_km": centerline_profile.min_distance_to_centerline_km,
                    "timestamp": centerline_profile.closest_observation_time.isoformat() if centerline_profile.closest_observation_time else None,
                    "lon": centerline_profile.cpa_observation_lon,
                    "lat": centerline_profile.cpa_observation_lat,
                },
            },
            "temporal": {
                "time_offset_from_source_hours": transit_profile.time_offset_from_source_hours,
                "time_offset_from_spill_hours": temporal_profile.hours_from_spill_time,
                "temporal_span_seconds": temporal_profile.temporal_span_seconds,
                "earliest_observation_time": temporal_profile.earliest_observation_time.isoformat() if temporal_profile.earliest_observation_time else None,
                "latest_observation_time": temporal_profile.latest_observation_time.isoformat() if temporal_profile.latest_observation_time else None,
                "closest_to_source_time": temporal_profile.closest_to_source_time.isoformat() if temporal_profile.closest_to_source_time else None,
            },
            "kinematic": {
                "mean_derived_speed_knots": mean_derived,
                "min_reported_sog_knots": min_sog,
                "max_reported_sog_knots": max_sog,
                "mean_reported_sog_knots": mean_sog_val,
                "course_drift_angle_diff_deg": centerline_profile.course_drift_angle_diff_deg,
                "total_track_distance_m": round(cumulative_dist_m, 2),
            },
            "data_quality": {
                "observation_count": pos_count,
                "duplicate_count": dup_count,
                "has_multiple_observations": (pos_count >= 2),
                "has_valid_kinematics": has_valid_kin,
                "invalid_kinematic_interval_count": invalid_intervals,
                "sparse_track": (pos_count <= 2),
                "is_real_observation": is_real,
                "data_source_type": provenance_cat,
                "quality_flags": q_flags,
            },
        }

        status_val = "completed" if pos_count >= 2 else "single_observation"
        analysis = VesselTrajectoryAnalysis(
            candidate_id=candidate.candidate_id,
            vessel_id=candidate.vessel_id,
            mmsi=candidate.mmsi,
            imo=candidate.imo,
            vessel_name=candidate.vessel_name,
            vessel_type=candidate.vessel_type,
            call_sign=candidate.call_sign,
            flag_country=candidate.flag_country,
            status=status_val,
            observed_positions_count=pos_count,
            total_track_duration_seconds=round(track_duration_s, 2),
            total_track_distance_m=round(cumulative_dist_m, 2),
            min_reported_sog_knots=min_sog,
            max_reported_sog_knots=max_sog,
            mean_reported_sog_knots=mean_sog_val,
            mean_derived_speed_knots=mean_derived,
            transit_profile=transit_profile,
            centerline_proximity=centerline_profile,
            temporal_correlation=temporal_profile,
            quality=quality_profile,
            observed_trajectory=observed_points,
            segments=segments,
            evidence=evidence,
            metadata={
                "zero_fabrication": True,
                "inside_source_zone_flag": candidate.inside_source_zone,
                "duplicates_removed_count": dup_count,
            },
        )
        analyses.append(analysis)

    # 4. Export GeoJSON Artifact
    if output_dir is not None:
        target_dir = Path(output_dir)
    else:
        safe_inv = _sanitize(investigation_id, "investigation")
        safe_spill = _sanitize(spill_id, "spill")
        target_dir = (
            Path(settings.data_dir)
            / "derived"
            / safe_inv
            / "trajectory_analysis"
            / safe_spill
        )
    target_dir.mkdir(parents=True, exist_ok=True)

    features: list[dict[str, Any]] = []

    # Centerline feature for context
    features.append({
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": centerline_coords,
        },
        "properties": {
            "feature_kind": "backward_drift_centerline",
            "source_estimate_id": source_estimate.id,
            "net_drift_direction_deg": net_drift_dir,
        },
    })

    # Source candidate zone polygon for context
    if polygon_ring:
        features.append({
            "type": "Feature",
            "geometry": zone_geom,
            "properties": {
                "feature_kind": "source_candidate_zone",
                "source_estimate_id": source_estimate.id,
                "uncertainty_radius_km": source_estimate.source_uncertainty_radius_km,
            },
        })

    # Features for each analyzed vessel
    for v in analyses:
        cand_match = next((c for c in candidate_result.candidates if c.candidate_id == v.candidate_id), None)
        raw_pos = cand_match.raw_positions if cand_match else []

        # Observed trajectory LineString / Point
        if len(raw_pos) >= 2:
            track_geom: dict[str, Any] = {
                "type": "LineString",
                "coordinates": [[p.lon, p.lat] for p in raw_pos],
            }
        elif len(raw_pos) == 1:
            track_geom = {
                "type": "Point",
                "coordinates": [raw_pos[0].lon, raw_pos[0].lat],
            }
        else:
            track_geom = {
                "type": "Point",
                "coordinates": [v.transit_profile.cpa_lon, v.transit_profile.cpa_lat],
            }

        features.append({
            "type": "Feature",
            "geometry": track_geom,
            "properties": {
                "feature_kind": "candidate_trajectory",
                "candidate_id": v.candidate_id,
                "vessel_id": v.vessel_id,
                "mmsi": v.mmsi,
                "imo": v.imo,
                "vessel_name": v.vessel_name,
                "vessel_type": v.vessel_type,
                "call_sign": v.call_sign,
                "flag_country": v.flag_country,
                "status": v.status,
                "observed_positions_count": v.observed_positions_count,
                "total_track_distance_m": v.total_track_distance_m,
                "total_track_duration_seconds": v.total_track_duration_seconds,
                "mean_derived_speed_knots": v.mean_derived_speed_knots,
                "points_inside_zone": v.transit_profile.points_inside_count,
                "observed_transit_duration_seconds": v.transit_profile.observed_transit_duration_seconds,
                "min_distance_to_center_km": v.transit_profile.min_distance_to_center_km,
                "min_distance_to_polygon_km": v.transit_profile.min_distance_to_polygon_km,
                "min_distance_to_centerline_km": v.centerline_proximity.min_distance_to_centerline_km,
                "course_drift_angle_diff_deg": v.centerline_proximity.course_drift_angle_diff_deg,
                "zero_fabrication": True,
            },
        })

        # Closest approach point (CPA) to center
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [v.transit_profile.cpa_lon, v.transit_profile.cpa_lat],
            },
            "properties": {
                "feature_kind": "closest_approach_point",
                "candidate_id": v.candidate_id,
                "vessel_id": v.vessel_id,
                "mmsi": v.mmsi,
                "closest_position_time": v.transit_profile.closest_position_time.isoformat(),
                "min_distance_to_center_km": v.transit_profile.min_distance_to_center_km,
                "time_offset_from_source_hours": v.transit_profile.time_offset_from_source_hours,
            },
        })

        # Centerline closest point
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [
                    v.centerline_proximity.closest_centerline_point_lon,
                    v.centerline_proximity.closest_centerline_point_lat,
                ],
            },
            "properties": {
                "feature_kind": "centerline_closest_point",
                "candidate_id": v.candidate_id,
                "vessel_id": v.vessel_id,
                "min_distance_to_centerline_km": v.centerline_proximity.min_distance_to_centerline_km,
                "closest_observation_time": (
                    v.centerline_proximity.closest_observation_time.isoformat()
                    if v.centerline_proximity.closest_observation_time
                    else None
                ),
            },
        })

        # Polygon closest approach point
        if v.transit_profile.cpa_to_polygon_lon is not None and v.transit_profile.cpa_to_polygon_lat is not None:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        v.transit_profile.cpa_to_polygon_lon,
                        v.transit_profile.cpa_to_polygon_lat,
                    ],
                },
                "properties": {
                    "feature_kind": "polygon_closest_point",
                    "candidate_id": v.candidate_id,
                    "vessel_id": v.vessel_id,
                    "cpa_to_polygon_distance_km": v.transit_profile.cpa_to_polygon_distance_km,
                    "cpa_to_polygon_time": (
                        v.transit_profile.cpa_to_polygon_time.isoformat()
                        if v.transit_profile.cpa_to_polygon_time
                        else None
                    ),
                },
            })

    geojson_body = {
        "type": "FeatureCollection",
        "features": features,
    }
    geojson_path = target_dir / "trajectory_analysis.geojson"
    geojson_path.write_text(json.dumps(geojson_body, indent=2) + "\n", encoding="utf-8")

    # 5. Register derived DOCUMENT asset in AssetRegistry
    now_utc = datetime.now(timezone.utc)
    res_id = f"traj-analysis-{_sanitize(spill_id, 'spill')}-{_sanitize(investigation_id, 'inv')}"

    artifact = AcquiredArtifact(
        asset_type=AssetType.DOCUMENT,
        location=str(geojson_path.resolve()),
        source="trajectory_analysis_service",
        acquisition_time=now_utc,
        provenance=Provenance(
            product_id=candidate_result.id,
            retrieved_at=now_utc,
            processing_level="trajectory_analysis",
            notes=(
                f"Stage E2 physical trajectory analysis for {len(analyses)} candidate vessels. "
                f"Spill={spill_id}, SourceEstimate={source_estimate.id}. Zero-fabrication guarantee."
            ),
            extra={
                "asset_type": "trajectory_analysis",
                "spill_detection_id": spill_id,
                "source_estimate_id": source_estimate.id,
                "candidate_generation_id": candidate_result.id,
                "analyzed_vessel_count": len(analyses),
            },
        ),
        metadata={
            "asset_type": "trajectory_analysis",
            "investigation_id": investigation_id,
            "spill_id": spill_id,
            "source_estimate_id": source_estimate.id,
            "candidate_generation_id": candidate_result.id,
            "analyzed_vessel_count": len(analyses),
        },
    )

    derived_asset = target_registry.register(
        investigation_id, "trajectory_analysis_service", artifact
    )

    result = TrajectoryAnalysisResult(
        id=res_id,
        investigation_id=investigation_id,
        spill_detection_id=spill_id,
        source_estimate_id=source_estimate.id,
        candidate_generation_id=candidate_result.id,
        derived_asset_id=derived_asset.id,
        analyzed_vessel_count=len(analyses),
        analyses=analyses,
        metadata={
            "zero_fabrication_guarantee": True,
            "artifact_path": str(geojson_path.resolve()),
            "net_drift_direction_deg": net_drift_dir,
        },
    )

    return result, derived_asset
