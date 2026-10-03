"""MARIS Real-Experiment — SAR ↔ AIS Spatiotemporal Matching Service (Phase 6.2).

Performs instantaneous dual-sensor correlation between physical bright radar targets
detected in calibrated Sentinel-1 SAR imagery and spatiotemporal AIS transponder
observations at the satellite acquisition epoch.

SCIENTIFIC PRINCIPLES & NEGATIVE CONSTRAINTS:
--------------------------------------------
1. DUAL-SENSOR OBSERVATION: Compares instantaneous physical radar scatterers (SAR)
   against cooperative radio transponder broadcasts (AIS). Discrepancies represent
   observational anomalies requiring investigation, NOT legal proof of intentional
   evasion, transponder tampering, or illicit activity.
2. ZERO-FABRICATION AIS ALIGNMENT:
   - Genuine fixes within coincident_time_threshold_s (default 60s) are used directly.
   - Linear interpolation is permitted ONLY when bounded by two genuine observations
     no more than max_interpolation_interval_s (default 900s / 15 minutes) apart.
   - Trajectories with gaps > 900s spanning the observation epoch are classified as
     AIS_OBSERVATION_GAP. Positions are NEVER fabricated across wide gaps.
   - Positions are NEVER extrapolated beyond the first or last genuine fix.
3. GATED NEAREST-NEIGHBOR WITH AMBIGUITY FLAGGING:
   - Coincident spatial gate: default 1,000 m (accommodates radar resolution + vessel motion).
   - Maximum association gate: default 3,000 m (bounds candidate search space).
   - Ambiguous multi-target proximity (multiple targets near 1 vessel or multiple vessels
     near 1 target) is explicitly flagged as AMBIGUOUS_MULTI_TARGET_PROXIMITY rather than
     arbitrarily forcing a 1-to-1 match.
4. STRICT READ-ONLY DATABASE ACCESS:
   - The AIS database (ais_vessels.db) is queried in read-only mode and NEVER modified.
5. NO UNPROVABLE QUANTITIES:
   - No Doppler coordinate displacement, orbital velocities, slant range, RCS, or hull dimensions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any

from app.services.real_experiment.ais_database import query_vessels_in_spatiotemporal_box
from app.services.real_experiment.sar_vessel_detector import SarBrightTarget


# Approved Neutral Discrepancy Classifications
CLASSIFICATION_COINCIDENT_MATCH = "COINCIDENT_AIS_MATCH"
CLASSIFICATION_SPATIAL_EXCEEDANCE = "SPATIAL_DISCREPANCY_EXCEEDANCE"
CLASSIFICATION_OBSERVATION_GAP = "AIS_OBSERVATION_GAP"
CLASSIFICATION_TARGET_UNCORRELATED = "RADAR_TARGET_UNCORRELATED"
CLASSIFICATION_VESSEL_NOT_DETECTED = "AIS_VESSEL_NOT_DETECTED"
CLASSIFICATION_AMBIGUOUS_PROXIMITY = "AMBIGUOUS_MULTI_TARGET_PROXIMITY"

APPROVED_CLASSIFICATIONS = {
    CLASSIFICATION_COINCIDENT_MATCH,
    CLASSIFICATION_SPATIAL_EXCEEDANCE,
    CLASSIFICATION_OBSERVATION_GAP,
    CLASSIFICATION_TARGET_UNCORRELATED,
    CLASSIFICATION_VESSEL_NOT_DETECTED,
    CLASSIFICATION_AMBIGUOUS_PROXIMITY,
}

DEFAULT_SURVEILLANCE_DISCLAIMER = (
    "NOTICE: SAR ↔ AIS spatiotemporal correlation reflects instantaneous dual-sensor observation "
    "at the satellite acquisition epoch. Discrepancies between physical radar backscatter and AIS "
    "transponder broadcasts are observational anomalies requiring investigation, NOT legal proof "
    "of transponder disabling, vessel identity, or illicit activity. Physical radar targets reflect "
    "scattering envelopes convolved with sensor PSF, not verified vessel hulls."
)


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two geographic points in meters."""
    r_earth = 6371000.0  # Mean Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return r_earth * c


def parse_utc_timestamp(ts: str | datetime) -> datetime:
    """Parse string or datetime to UTC timezone-aware datetime."""
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    # Parse ISO 8601 string
    clean_ts = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(clean_ts)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def interpolate_cog(cog1: float | None, cog2: float | None, alpha: float) -> float | None:
    """Interpolate Course Over Ground (degrees) taking shortest angular distance across 360 wrap."""
    if cog1 is None or cog2 is None:
        return cog1 if cog1 is not None else cog2
    diff = (cog2 - cog1 + 180.0) % 360.0 - 180.0
    return round((cog1 + alpha * diff) % 360.0, 1)


@dataclass
class AlignedAisPosition:
    """Temporally aligned AIS vessel state at the satellite observation epoch."""
    vessel_id: str
    mmsi: str
    vessel_name: str
    vessel_type: str | None
    lat: float
    lon: float
    timestamp_iso: str
    sog_knots: float | None
    cog_degrees: float | None
    alignment_method: str  # "GENUINE_OBSERVATION" or "TEMPORALLY_ALIGNED_INTERPOLATION"
    time_offset_seconds: float
    bounding_gap_seconds: float | None = None
    is_interpolated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "vessel_id": self.vessel_id,
            "mmsi": self.mmsi,
            "vessel_name": self.vessel_name,
            "vessel_type": self.vessel_type,
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
            "timestamp_iso": self.timestamp_iso,
            "sog_knots": round(self.sog_knots, 1) if self.sog_knots is not None else None,
            "cog_degrees": round(self.cog_degrees, 1) if self.cog_degrees is not None else None,
            "alignment_method": self.alignment_method,
            "time_offset_seconds": round(self.time_offset_seconds, 1),
            "bounding_gap_seconds": (
                round(self.bounding_gap_seconds, 1) if self.bounding_gap_seconds is not None else None
            ),
            "is_interpolated": self.is_interpolated,
        }


@dataclass
class SarAisAssociation:
    """Represents a spatiotemporal correlation or discrepancy between SAR and AIS."""
    association_id: str
    classification: str
    target_id: str | None = None
    vessel_id: str | None = None
    mmsi: str | None = None
    vessel_name: str | None = None
    distance_meters: float | None = None
    target_lat: float | None = None
    target_lon: float | None = None
    target_peak_db: float | None = None
    target_tcr_db: float | None = None
    ais_lat: float | None = None
    ais_lon: float | None = None
    ais_sog_knots: float | None = None
    ais_cog_degrees: float | None = None
    ais_alignment_method: str | None = None
    ais_time_offset_seconds: float | None = None
    ais_gap_seconds: float | None = None
    ambiguous_candidate_ids: list[str] = field(default_factory=list)
    ambiguous_distances_m: list[float] = field(default_factory=list)
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "association_id": self.association_id,
            "classification": self.classification,
            "target_id": self.target_id,
            "vessel_id": self.vessel_id,
            "mmsi": self.mmsi,
            "vessel_name": self.vessel_name,
            "distance_meters": round(self.distance_meters, 1) if self.distance_meters is not None else None,
            "target_lat": round(self.target_lat, 6) if self.target_lat is not None else None,
            "target_lon": round(self.target_lon, 6) if self.target_lon is not None else None,
            "target_peak_db": round(self.target_peak_db, 2) if self.target_peak_db is not None else None,
            "target_tcr_db": round(self.target_tcr_db, 2) if self.target_tcr_db is not None else None,
            "ais_lat": round(self.ais_lat, 6) if self.ais_lat is not None else None,
            "ais_lon": round(self.ais_lon, 6) if self.ais_lon is not None else None,
            "ais_sog_knots": round(self.ais_sog_knots, 1) if self.ais_sog_knots is not None else None,
            "ais_cog_degrees": round(self.ais_cog_degrees, 1) if self.ais_cog_degrees is not None else None,
            "ais_alignment_method": self.ais_alignment_method,
            "ais_time_offset_seconds": (
                round(self.ais_time_offset_seconds, 1) if self.ais_time_offset_seconds is not None else None
            ),
            "ais_gap_seconds": round(self.ais_gap_seconds, 1) if self.ais_gap_seconds is not None else None,
            "ambiguous_candidate_ids": self.ambiguous_candidate_ids,
            "ambiguous_distances_m": [round(d, 1) for d in self.ambiguous_distances_m],
            "notes": self.notes,
        }


@dataclass
class SarAisMatchingResult:
    """Full results of the SAR ↔ AIS spatiotemporal correlation analysis."""
    observation_time_iso: str
    scene_bbox: tuple[float, float, float, float]
    total_sar_targets: int
    total_ais_candidates: int
    matched_coincident_count: int
    spatial_discrepancy_count: int
    uncorrelated_target_count: int
    undetected_vessel_count: int
    observation_gap_count: int
    ambiguous_count: int
    associations: list[SarAisAssociation]
    parameters: dict[str, Any]
    scientific_disclaimer: str = DEFAULT_SURVEILLANCE_DISCLAIMER

    def as_dict(self) -> dict[str, Any]:
        return {
            "observation_time_iso": self.observation_time_iso,
            "scene_bbox": [round(c, 5) for c in self.scene_bbox],
            "total_sar_targets": self.total_sar_targets,
            "total_ais_candidates": self.total_ais_candidates,
            "matched_coincident_count": self.matched_coincident_count,
            "spatial_discrepancy_count": self.spatial_discrepancy_count,
            "uncorrelated_target_count": self.uncorrelated_target_count,
            "undetected_vessel_count": self.undetected_vessel_count,
            "observation_gap_count": self.observation_gap_count,
            "ambiguous_count": self.ambiguous_count,
            "associations": [a.as_dict() for a in self.associations],
            "parameters": self.parameters,
            "scientific_disclaimer": self.scientific_disclaimer,
        }


class SarAisMatchingService:
    """Spatiotemporal correlation service using Gated Nearest-Neighbor with Ambiguity Flagging."""

    def __init__(
        self,
        *,
        coincident_spatial_gate_m: float = 1000.0,
        max_association_gate_m: float = 3000.0,
        temporal_window_minutes: float = 30.0,
        max_interpolation_interval_s: float = 900.0,
        coincident_time_threshold_s: float = 60.0,
    ) -> None:
        """Initialize the correlation service with configurable gates.

        Args:
            coincident_spatial_gate_m: Distance threshold (meters) for COINCIDENT_AIS_MATCH (default 1,000m).
            max_association_gate_m: Max search gate (meters) for SPATIAL_DISCREPANCY_EXCEEDANCE (default 3,000m).
            temporal_window_minutes: Search window half-width in minutes around t_obs (default 30m).
            max_interpolation_interval_s: Max time gap (seconds) across which linear interpolation is permitted (default 900s).
            coincident_time_threshold_s: Max time delta (seconds) to use a genuine fix directly without interpolation (default 60s).
        """
        if coincident_spatial_gate_m <= 0.0:
            raise ValueError("coincident_spatial_gate_m must be positive")
        if max_association_gate_m < coincident_spatial_gate_m:
            raise ValueError("max_association_gate_m must be greater than or equal to coincident_spatial_gate_m")
        if temporal_window_minutes <= 0.0:
            raise ValueError("temporal_window_minutes must be positive")
        if max_interpolation_interval_s <= 0.0:
            raise ValueError("max_interpolation_interval_s must be positive")
        if coincident_time_threshold_s < 0.0:
            raise ValueError("coincident_time_threshold_s must be non-negative")

        self.coincident_spatial_gate_m = float(coincident_spatial_gate_m)
        self.max_association_gate_m = float(max_association_gate_m)
        self.temporal_window_minutes = float(temporal_window_minutes)
        self.max_interpolation_interval_s = float(max_interpolation_interval_s)
        self.coincident_time_threshold_s = float(coincident_time_threshold_s)

    def align_vessel_position(
        self,
        vessel: dict[str, Any],
        observation_time: datetime,
    ) -> tuple[AlignedAisPosition | None, str, dict[str, Any]]:
        """Temporally align an AIS vessel to the observation epoch without data fabrication.

        Returns:
            tuple of (aligned_position_or_none, alignment_status, metadata_dict)
            Status is one of:
            - "GENUINE_OBSERVATION": an observation within coincident_time_threshold_s was used directly.
            - "TEMPORALLY_ALIGNED_INTERPOLATION": linearly interpolated between two bounding fixes <= 900s apart.
            - "AIS_OBSERVATION_GAP": bounding gap > 900s; no position fabricated.
            - "OUTSIDE_TEMPORAL_WINDOW": track does not bound t_obs; extrapolation prohibited.
        """
        t_obs = parse_utc_timestamp(observation_time)
        positions = vessel.get("positions", [])
        vessel_id = str(vessel.get("vessel_id") or vessel.get("id") or "UNKNOWN")
        mmsi = str(vessel.get("mmsi") or "")
        vessel_name = str(vessel.get("vessel_name") or f"MMSI-{mmsi}")
        vessel_type = vessel.get("vessel_type")

        if not positions:
            return None, "NO_POSITIONS", {"vessel_id": vessel_id, "mmsi": mmsi}

        # Parse and sort positions by UTC timestamp
        parsed_positions = []
        for p in positions:
            ts = parse_utc_timestamp(p["timestamp"])
            parsed_positions.append((ts, p))
        parsed_positions.sort(key=lambda item: item[0])

        # 1. Check for genuine coincident observation (|t - t_obs| <= coincident_time_threshold_s)
        best_delta = float("inf")
        best_pos = None
        best_ts = None
        for ts, p in parsed_positions:
            delta = abs((ts - t_obs).total_seconds())
            if delta < best_delta:
                best_delta = delta
                best_pos = p
                best_ts = ts

        if best_delta <= self.coincident_time_threshold_s and best_pos is not None and best_ts is not None:
            sog = best_pos.get("speed") if best_pos.get("speed") is not None else best_pos.get("sog")
            return (
                AlignedAisPosition(
                    vessel_id=vessel_id,
                    mmsi=mmsi,
                    vessel_name=vessel_name,
                    vessel_type=vessel_type,
                    lat=float(best_pos["lat"]),
                    lon=float(best_pos["lon"]),
                    timestamp_iso=best_ts.isoformat().replace("+00:00", "Z"),
                    sog_knots=float(sog) if sog is not None else None,
                    cog_degrees=float(best_pos["cog"]) if best_pos.get("cog") is not None else None,
                    alignment_method="GENUINE_OBSERVATION",
                    time_offset_seconds=best_delta,
                    bounding_gap_seconds=None,
                    is_interpolated=False,
                ),
                "GENUINE_OBSERVATION",
                {"time_offset_s": best_delta},
            )

        # 2. Check if t_obs is bounded by two genuine observations
        t_first = parsed_positions[0][0]
        t_last = parsed_positions[-1][0]

        # Rule: NEVER extrapolate beyond track bounds
        if t_obs < t_first or t_obs > t_last:
            return None, "OUTSIDE_TEMPORAL_WINDOW", {
                "t_first": t_first.isoformat(),
                "t_last": t_last.isoformat(),
                "t_obs": t_obs.isoformat(),
            }

        # Find the two bounding positions t_prev <= t_obs <= t_next
        prev_item = None
        next_item = None
        for i in range(len(parsed_positions) - 1):
            if parsed_positions[i][0] <= t_obs <= parsed_positions[i + 1][0]:
                prev_item = parsed_positions[i]
                next_item = parsed_positions[i + 1]
                break

        if prev_item is None or next_item is None:
            return None, "OUTSIDE_TEMPORAL_WINDOW", {}

        t_prev, p_prev = prev_item
        t_next, p_next = next_item
        gap_seconds = (t_next - t_prev).total_seconds()

        # Rule: Linear interpolation permitted ONLY if gap <= max_interpolation_interval_s (900s)
        if gap_seconds > self.max_interpolation_interval_s:
            return None, CLASSIFICATION_OBSERVATION_GAP, {
                "gap_seconds": gap_seconds,
                "max_allowed_gap_s": self.max_interpolation_interval_s,
                "t_prev": t_prev.isoformat(),
                "t_next": t_next.isoformat(),
            }

        # Perform bounded linear interpolation
        alpha = 0.0 if gap_seconds <= 0.0 else (t_obs - t_prev).total_seconds() / gap_seconds
        alpha = max(0.0, min(1.0, alpha))

        interp_lat = float(p_prev["lat"]) + alpha * (float(p_next["lat"]) - float(p_prev["lat"]))
        interp_lon = float(p_prev["lon"]) + alpha * (float(p_next["lon"]) - float(p_prev["lon"]))

        sog_prev = p_prev.get("speed") if p_prev.get("speed") is not None else p_prev.get("sog")
        sog_next = p_next.get("speed") if p_next.get("speed") is not None else p_next.get("sog")
        interp_sog = None
        if sog_prev is not None and sog_next is not None:
            interp_sog = float(sog_prev) + alpha * (float(sog_next) - float(sog_prev))
        elif sog_prev is not None:
            interp_sog = float(sog_prev)

        cog_prev = p_prev.get("cog")
        cog_next = p_next.get("cog")
        interp_cog = interpolate_cog(cog_prev, cog_next, alpha)

        time_offset = min((t_obs - t_prev).total_seconds(), (t_next - t_obs).total_seconds())

        return (
            AlignedAisPosition(
                vessel_id=vessel_id,
                mmsi=mmsi,
                vessel_name=vessel_name,
                vessel_type=vessel_type,
                lat=interp_lat,
                lon=interp_lon,
                timestamp_iso=t_obs.isoformat().replace("+00:00", "Z"),
                sog_knots=interp_sog,
                cog_degrees=interp_cog,
                alignment_method="TEMPORALLY_ALIGNED_INTERPOLATION",
                time_offset_seconds=time_offset,
                bounding_gap_seconds=gap_seconds,
                is_interpolated=True,
            ),
            "TEMPORALLY_ALIGNED_INTERPOLATION",
            {"gap_seconds": gap_seconds, "alpha": alpha},
        )

    def correlate(
        self,
        *,
        sar_targets: list[SarBrightTarget],
        ais_vessels: list[dict[str, Any]],
        observation_time: datetime,
        scene_bbox: tuple[float, float, float, float],
    ) -> SarAisMatchingResult:
        """Run Gated Nearest-Neighbor correlation with Ambiguity Flagging.

        Args:
            sar_targets: List of detected SarBrightTarget objects.
            ais_vessels: List of candidate vessel dicts with AIS tracks.
            observation_time: Satellite acquisition epoch (datetime).
            scene_bbox: (west, south, east, north) in WGS84 degrees.
        """
        t_obs = parse_utc_timestamp(observation_time)
        t_obs_iso = t_obs.isoformat().replace("+00:00", "Z")

        # 1. Temporally align all candidate AIS vessels
        aligned_vessels: list[AlignedAisPosition] = []
        gap_vessels: list[dict[str, Any]] = []

        for v in ais_vessels:
            pos, status, meta = self.align_vessel_position(v, t_obs)
            if status == CLASSIFICATION_OBSERVATION_GAP:
                gap_vessels.append({
                    "vessel": v,
                    "gap_seconds": meta.get("gap_seconds"),
                })
            elif pos is not None:
                aligned_vessels.append(pos)

        # 2. Build Candidate Distance Matrix within max_association_gate_m
        # candidate_pairs: list of (target_idx, vessel_idx, distance_m)
        candidate_pairs: list[tuple[int, int, float]] = []
        for t_idx, tgt in enumerate(sar_targets):
            for v_idx, vsl in enumerate(aligned_vessels):
                dist_m = haversine_distance_m(tgt.lat, tgt.lon, vsl.lat, vsl.lon)
                if dist_m <= self.max_association_gate_m:
                    candidate_pairs.append((t_idx, v_idx, dist_m))

        # Adjacency maps
        target_to_vessels: dict[int, list[tuple[int, float]]] = {i: [] for i in range(len(sar_targets))}
        vessel_to_targets: dict[int, list[tuple[int, float]]] = {j: [] for j in range(len(aligned_vessels))}

        for t_idx, v_idx, dist_m in candidate_pairs:
            target_to_vessels[t_idx].append((v_idx, dist_m))
            vessel_to_targets[v_idx].append((t_idx, dist_m))

        # 3. Detect Ambiguous Many-to-Many / Multi-Target Clusters
        ambiguous_target_indices: set[int] = set()
        ambiguous_vessel_indices: set[int] = set()

        for t_idx, v_list in target_to_vessels.items():
            if len(v_list) > 1:
                ambiguous_target_indices.add(t_idx)
                for v_idx, _ in v_list:
                    ambiguous_vessel_indices.add(v_idx)

        for v_idx, t_list in vessel_to_targets.items():
            if len(t_list) > 1:
                ambiguous_vessel_indices.add(v_idx)
                for t_idx, _ in t_list:
                    ambiguous_target_indices.add(t_idx)

        associations: list[SarAisAssociation] = []
        assoc_counter = 1

        # 4. Record Ambiguous Multi-Target Associations
        for t_idx in sorted(ambiguous_target_indices):
            tgt = sar_targets[t_idx]
            v_list = target_to_vessels[t_idx]
            v_list.sort(key=lambda item: item[1])
            cand_ids = [aligned_vessels[v_idx].vessel_id for v_idx, _ in v_list]
            cand_distances = [dist for _, dist in v_list]
            min_dist = cand_distances[0] if cand_distances else None

            associations.append(
                SarAisAssociation(
                    association_id=f"ASC-{assoc_counter:03d}",
                    classification=CLASSIFICATION_AMBIGUOUS_PROXIMITY,
                    target_id=tgt.target_id,
                    vessel_id=None,
                    distance_meters=min_dist,
                    target_lat=tgt.lat,
                    target_lon=tgt.lon,
                    target_peak_db=tgt.peak_backscatter_db,
                    target_tcr_db=tgt.target_to_clutter_ratio_db,
                    ambiguous_candidate_ids=cand_ids,
                    ambiguous_distances_m=cand_distances,
                    notes=(
                        f"Ambiguous proximity: Radar target {tgt.target_id} has {len(cand_ids)} candidate "
                        f"AIS vessels within {self.max_association_gate_m:.0f}m association gate. "
                        "Relationship not arbitrarily resolved."
                    ),
                )
            )
            assoc_counter += 1

        # 5. Process Unambiguous Associations (1-to-1 pairs)
        matched_target_indices: set[int] = set(ambiguous_target_indices)
        matched_vessel_indices: set[int] = set(ambiguous_vessel_indices)

        for t_idx, v_list in target_to_vessels.items():
            if t_idx in matched_target_indices:
                continue
            if len(v_list) == 1:
                v_idx, dist_m = v_list[0]
                if v_idx not in matched_vessel_indices and len(vessel_to_targets[v_idx]) == 1:
                    matched_target_indices.add(t_idx)
                    matched_vessel_indices.add(v_idx)
                    tgt = sar_targets[t_idx]
                    vsl = aligned_vessels[v_idx]

                    if dist_m <= self.coincident_spatial_gate_m:
                        classification = CLASSIFICATION_COINCIDENT_MATCH
                        notes = (
                            f"Coincident match within {self.coincident_spatial_gate_m:.0f}m gate "
                            f"(distance: {dist_m:.1f}m)."
                        )
                    else:
                        classification = CLASSIFICATION_SPATIAL_EXCEEDANCE
                        notes = (
                            f"Spatial discrepancy: distance ({dist_m:.1f}m) exceeds coincident gate "
                            f"({self.coincident_spatial_gate_m:.0f}m) but within maximum association gate "
                            f"({self.max_association_gate_m:.0f}m)."
                        )

                    associations.append(
                        SarAisAssociation(
                            association_id=f"ASC-{assoc_counter:03d}",
                            classification=classification,
                            target_id=tgt.target_id,
                            vessel_id=vsl.vessel_id,
                            mmsi=vsl.mmsi,
                            vessel_name=vsl.vessel_name,
                            distance_meters=dist_m,
                            target_lat=tgt.lat,
                            target_lon=tgt.lon,
                            target_peak_db=tgt.peak_backscatter_db,
                            target_tcr_db=tgt.target_to_clutter_ratio_db,
                            ais_lat=vsl.lat,
                            ais_lon=vsl.lon,
                            ais_sog_knots=vsl.sog_knots,
                            ais_cog_degrees=vsl.cog_degrees,
                            ais_alignment_method=vsl.alignment_method,
                            ais_time_offset_seconds=vsl.time_offset_seconds,
                            ais_gap_seconds=vsl.bounding_gap_seconds,
                            notes=notes,
                        )
                    )
                    assoc_counter += 1

        # 6. Uncorrelated Radar Targets (no AIS within max_association_gate_m)
        for t_idx, tgt in enumerate(sar_targets):
            if t_idx in matched_target_indices:
                continue
            associations.append(
                SarAisAssociation(
                    association_id=f"ASC-{assoc_counter:03d}",
                    classification=CLASSIFICATION_TARGET_UNCORRELATED,
                    target_id=tgt.target_id,
                    target_lat=tgt.lat,
                    target_lon=tgt.lon,
                    target_peak_db=tgt.peak_backscatter_db,
                    target_tcr_db=tgt.target_to_clutter_ratio_db,
                    notes=(
                        f"Bright radar target with no AIS transponder correlation within "
                        f"{self.max_association_gate_m:.0f}m search gate."
                    ),
                )
            )
            assoc_counter += 1

        # 7. Undetected AIS Vessels (aligned vessel in scene bbox, but no radar target within max gate)
        west, south, east, north = scene_bbox
        for v_idx, vsl in enumerate(aligned_vessels):
            if v_idx in matched_vessel_indices:
                continue
            # Check if vessel was inside scene footprint at t_obs
            if west <= vsl.lon <= east and south <= vsl.lat <= north:
                associations.append(
                    SarAisAssociation(
                        association_id=f"ASC-{assoc_counter:03d}",
                        classification=CLASSIFICATION_VESSEL_NOT_DETECTED,
                        vessel_id=vsl.vessel_id,
                        mmsi=vsl.mmsi,
                        vessel_name=vsl.vessel_name,
                        ais_lat=vsl.lat,
                        ais_lon=vsl.lon,
                        ais_sog_knots=vsl.sog_knots,
                        ais_cog_degrees=vsl.cog_degrees,
                        ais_alignment_method=vsl.alignment_method,
                        ais_time_offset_seconds=vsl.time_offset_seconds,
                        ais_gap_seconds=vsl.bounding_gap_seconds,
                        notes=(
                            f"AIS vessel present in scene footprint at acquisition epoch has no corresponding "
                            f"bright radar target detected within {self.max_association_gate_m:.0f}m."
                        ),
                    )
                )
                assoc_counter += 1

        # 8. AIS Observation Gaps (track spanned t_obs but gap > 900s prevented safe interpolation)
        for g in gap_vessels:
            v_dict = g["vessel"]
            v_id = str(v_dict.get("vessel_id") or v_dict.get("id") or "UNKNOWN")
            mmsi = str(v_dict.get("mmsi") or "")
            v_name = str(v_dict.get("vessel_name") or f"MMSI-{mmsi}")
            gap_s = g.get("gap_seconds")

            associations.append(
                SarAisAssociation(
                    association_id=f"ASC-{assoc_counter:03d}",
                    classification=CLASSIFICATION_OBSERVATION_GAP,
                    vessel_id=v_id,
                    mmsi=mmsi,
                    vessel_name=v_name,
                    ais_gap_seconds=gap_s,
                    notes=(
                        f"AIS observation gap ({gap_s:.0f}s) across observation epoch exceeds maximum safe "
                        f"interpolation threshold ({self.max_interpolation_interval_s:.0f}s). "
                        "Position not fabricated."
                    ),
                )
            )
            assoc_counter += 1

        # 9. Aggregate classification summary counts
        counts = {
            CLASSIFICATION_COINCIDENT_MATCH: 0,
            CLASSIFICATION_SPATIAL_EXCEEDANCE: 0,
            CLASSIFICATION_TARGET_UNCORRELATED: 0,
            CLASSIFICATION_VESSEL_NOT_DETECTED: 0,
            CLASSIFICATION_OBSERVATION_GAP: 0,
            CLASSIFICATION_AMBIGUOUS_PROXIMITY: 0,
        }
        for a in associations:
            if a.classification in counts:
                counts[a.classification] += 1

        return SarAisMatchingResult(
            observation_time_iso=t_obs_iso,
            scene_bbox=scene_bbox,
            total_sar_targets=len(sar_targets),
            total_ais_candidates=len(ais_vessels),
            matched_coincident_count=counts[CLASSIFICATION_COINCIDENT_MATCH],
            spatial_discrepancy_count=counts[CLASSIFICATION_SPATIAL_EXCEEDANCE],
            uncorrelated_target_count=counts[CLASSIFICATION_TARGET_UNCORRELATED],
            undetected_vessel_count=counts[CLASSIFICATION_VESSEL_NOT_DETECTED],
            observation_gap_count=counts[CLASSIFICATION_OBSERVATION_GAP],
            ambiguous_count=counts[CLASSIFICATION_AMBIGUOUS_PROXIMITY],
            associations=associations,
            parameters={
                "coincident_spatial_gate_m": self.coincident_spatial_gate_m,
                "max_association_gate_m": self.max_association_gate_m,
                "temporal_window_minutes": self.temporal_window_minutes,
                "max_interpolation_interval_s": self.max_interpolation_interval_s,
                "coincident_time_threshold_s": self.coincident_time_threshold_s,
            },
        )

    def correlate_from_db(
        self,
        *,
        sar_targets: list[SarBrightTarget],
        observation_time: datetime,
        scene_bbox: tuple[float, float, float, float],
        db_path: Path | str | None = None,
    ) -> SarAisMatchingResult:
        """Query ais_vessels.db in read-only mode and run correlation."""
        west, south, east, north = scene_bbox
        # Add a buffer corresponding to max_association_gate_m (~3km ≈ 0.035 deg)
        deg_buf = (self.max_association_gate_m / 111000.0) * 1.5

        t_obs = parse_utc_timestamp(observation_time)
        delta_t = self.temporal_window_minutes * 60.0
        from datetime import timedelta
        t_start = t_obs - timedelta(seconds=delta_t)
        t_end = t_obs + timedelta(seconds=delta_t)

        candidates = query_vessels_in_spatiotemporal_box(
            lat_min=south - deg_buf,
            lat_max=north + deg_buf,
            lon_min=west - deg_buf,
            lon_max=east + deg_buf,
            t_start=t_start,
            t_end=t_end,
            db_path=Path(db_path) if db_path is not None else None,
        )

        return self.correlate(
            sar_targets=sar_targets,
            ais_vessels=candidates,
            observation_time=observation_time,
            scene_bbox=scene_bbox,
        )
