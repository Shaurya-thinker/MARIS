"""Feature engineering for maritime spill attribution.

Extracts 10 physically grounded numerical features for each candidate vessel
relative to the reconstructed backward drift trajectory and source zone.

Features:
1. min_source_distance_km: Minimum distance (km) from vessel positions to the reconstructed source zone centroid.
2. temporal_overlap_hours: Duration (hours) of vessel AIS reports inside the backtrack time window.
3. trajectory_overlap_fraction: Fraction of vessel AIS positions within the source uncertainty radius.
4. heading_consistency: Geometric agreement between vessel course and the reconstructed drift vector at closest approach.
   NOTE: This measures trajectory orientation relative to the drift track without assuming heading alone proves culpability.
5. speed_consistency: Score [0.0, 1.0] evaluating whether vessel speed matches standard maritime transit/discharge operations (1-15 knots).
6. ais_position_count: Total count of AIS positions recorded during the backtrack window.
7. ais_coverage_fraction: Completeness of AIS reporting relative to expected transmission cadence (10 reports/hour).
8. reconstructed_source_proximity: Exponential decay score exp(-d_min / R_source) reflecting spatial confidence.
9. drift_trajectory_min_distance_km: Minimum distance (km) to the continuous backward drift trajectory line.
10. time_difference_hours: Absolute time difference (hours) between vessel closest approach and estimated release time.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

FEATURE_NAMES: list[str] = [
    "min_source_distance_km",
    "temporal_overlap_hours",
    "trajectory_overlap_fraction",
    "heading_consistency",
    "speed_consistency",
    "ais_position_count",
    "ais_coverage_fraction",
    "reconstructed_source_proximity",
    "drift_trajectory_min_distance_km",
    "time_difference_hours",
]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute great-circle distance between two WGS-84 points in kilometers."""
    r_earth = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(max(0.0, a)), math.sqrt(max(0.0, 1.0 - a)))
    return r_earth * c


def _utc(dt: datetime) -> datetime:
    """Ensure datetime is UTC-aware."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def extract_vessel_features(
    vessel_data: dict[str, Any],
    source_lon: float,
    source_lat: float,
    source_radius_m: float,
    observation_time: datetime,
    backtrack_hours: float,
    backward_steps: list[Any],
) -> dict[str, float]:
    """Extract 10 normalized/scaled physical features for a candidate vessel.

    All returned values are guaranteed to be finite floats suitable for ML tabular input.
    """
    obs_time = _utc(observation_time)
    release_time = obs_time - timedelta(hours=backtrack_hours)
    window_start = release_time - timedelta(hours=1.0)
    window_end = obs_time + timedelta(hours=1.0)
    source_radius_km = max(source_radius_m / 1000.0, 0.5)

    positions_raw = vessel_data.get("positions", [])
    parsed_positions: list[dict[str, Any]] = []

    for pos in positions_raw:
        try:
            ts = pos.get("timestamp")
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            lat = float(pos.get("lat", float("nan")))
            lon = float(pos.get("lon", float("nan")))
            if ts and math.isfinite(lat) and math.isfinite(lon):
                parsed_positions.append({
                    "time": _utc(ts),
                    "lat": lat,
                    "lon": lon,
                    "speed": float(pos["speed"]) if pos.get("speed") is not None else None,
                    "heading": float(pos["heading"]) if pos.get("heading") is not None else None,
                })
        except Exception:
            continue

    parsed_positions.sort(key=lambda p: p["time"])

    # Fallback default features if no AIS positions available
    if not parsed_positions:
        return {
            "min_source_distance_km": 500.0,
            "temporal_overlap_hours": 0.0,
            "trajectory_overlap_fraction": 0.0,
            "heading_consistency": 0.5,
            "speed_consistency": 0.0,
            "ais_position_count": 0.0,
            "ais_coverage_fraction": 0.0,
            "reconstructed_source_proximity": 0.0,
            "drift_trajectory_min_distance_km": 500.0,
            "time_difference_hours": 24.0,
        }

    # Positions within or near the backtrack window
    window_positions = [
        p for p in parsed_positions
        if window_start <= p["time"] <= window_end
    ]
    active_positions = window_positions if window_positions else parsed_positions

    # 1. Minimum distance to source centroid
    min_source_dist = float("inf")
    closest_pos = active_positions[0]
    for p in active_positions:
        d = haversine_km(p["lat"], p["lon"], source_lat, source_lon)
        if d < min_source_dist:
            min_source_dist = d
            closest_pos = p

    # 2. Temporal overlap (hours between first and last report in window)
    if window_positions:
        t_first = window_positions[0]["time"]
        t_last = window_positions[-1]["time"]
        temporal_overlap_h = max(0.0, (t_last - t_first).total_seconds() / 3600.0)
    else:
        temporal_overlap_h = 0.0

    # 3. Trajectory overlap fraction (fraction of points within source radius)
    inside_count = sum(
        1 for p in active_positions
        if haversine_km(p["lat"], p["lon"], source_lat, source_lon) <= source_radius_km
    )
    trajectory_overlap = inside_count / len(active_positions)

    # 4. Heading consistency
    # Geometric heading alignment:
    # Measures the geometric relationship between the vessel's motion and the backward drift track.
    # If the vessel trajectory runs along or across the drift direction, we calculate angular difference.
    # Neutral default 0.5 if heading data is missing.
    heading_consistency = 0.5
    if closest_pos.get("heading") is not None and len(backward_steps) >= 2:
        first_step = backward_steps[0]
        step_lon = getattr(first_step, "lon", None)
        if step_lon is None and isinstance(first_step, dict):
            step_lon = first_step.get("lon")
        if step_lon is None:
            step_lon = source_lon

        step_lat = getattr(first_step, "lat", None)
        if step_lat is None and isinstance(first_step, dict):
            step_lat = first_step.get("lat")
        if step_lat is None:
            step_lat = source_lat

        drift_dx = source_lon - step_lon
        drift_dy = source_lat - step_lat
        drift_track_angle = math.degrees(math.atan2(drift_dx, drift_dy)) % 360.0

        vessel_heading = float(closest_pos["heading"]) % 360.0
        angle_diff = abs(vessel_heading - drift_track_angle) % 360.0
        if angle_diff > 180.0:
            angle_diff = 360.0 - angle_diff
        # Continuous metric [0.0, 1.0] indicating alignment with reconstructed track
        heading_consistency = 1.0 - (angle_diff / 180.0)

    # 5. Speed consistency (typical transit / discharge speeds 1 - 15 knots)
    speeds = [p["speed"] for p in active_positions if p.get("speed") is not None]
    if speeds:
        avg_speed = sum(speeds) / len(speeds)
        if 0.5 <= avg_speed <= 16.0:
            speed_consistency = 1.0 - abs(avg_speed - 10.0) / 25.0
        elif avg_speed < 0.5:
            speed_consistency = 0.7  # Anchored or loitering
        else:
            speed_consistency = max(0.0, 1.0 - (avg_speed - 16.0) / 20.0)
    else:
        speed_consistency = 0.5

    # 6. AIS position count
    ais_pos_count = float(len(window_positions))

    # 7. AIS coverage fraction
    expected_reports = max(backtrack_hours * 10.0, 1.0)
    ais_coverage = min(1.0, ais_pos_count / expected_reports)

    # 8. Reconstructed source proximity (smooth exponential decay)
    reconstructed_source_proximity = math.exp(-min_source_dist / max(source_radius_km, 2.0))

    # 9. Drift trajectory min distance (to any step in the backward drift track)
    drift_trajectory_min_distance_km = float("inf")
    for step in backward_steps:
        s_lat = getattr(step, "lat", None)
        s_lon = getattr(step, "lon", None)
        if s_lat is None and isinstance(step, dict):
            s_lat = step.get("lat")
            s_lon = step.get("lon")
        if s_lat is not None and s_lon is not None:
            for p in active_positions:
                d = haversine_km(p["lat"], p["lon"], s_lat, s_lon)
                if d < drift_trajectory_min_distance_km:
                    drift_trajectory_min_distance_km = d
    if not math.isfinite(drift_trajectory_min_distance_km):
        drift_trajectory_min_distance_km = min_source_dist

    # 10. Time difference hours between closest approach and estimated release time
    time_difference_hours = abs((closest_pos["time"] - release_time).total_seconds()) / 3600.0

    return {
        "min_source_distance_km": float(round(min_source_dist, 4)),
        "temporal_overlap_hours": float(round(temporal_overlap_h, 4)),
        "trajectory_overlap_fraction": float(round(trajectory_overlap, 4)),
        "heading_consistency": float(round(heading_consistency, 4)),
        "speed_consistency": float(round(speed_consistency, 4)),
        "ais_position_count": float(ais_pos_count),
        "ais_coverage_fraction": float(round(ais_coverage, 4)),
        "reconstructed_source_proximity": float(round(reconstructed_source_proximity, 4)),
        "drift_trajectory_min_distance_km": float(round(drift_trajectory_min_distance_km, 4)),
        "time_difference_hours": float(round(time_difference_hours, 4)),
    }
