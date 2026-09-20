"""MARIS Real-Experiment — Experiment Runner.

Orchestrates a complete real-data attribution experiment:

    1. Open ERA5 and CMEMS NetCDF files (provided by EnvironmentSelectorService).
    2. Run the existing backward drift engine (Stage D3 source_estimation).
    3. Score each selected vessel's AIS track against the reconstructed source zone.
    4. Rank candidates by evidence consistency score.
    5. Return a structured ExperimentResult that the persistence layer can save.

NO hardcoded vessel scores.
NO hardcoded source coordinates.
NO hardcoded rankings.

All output values are computed from the supplied input data.  Changing the
satellite observation, environmental data, or vessel list MUST change the result.

Method label: "Physics + feature-based attribution baseline (not a trained ML model)"
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models.asset import Asset
from app.models.common import AssetType, Provenance
from app.models.source_estimation import SourceEstimateResult
from app.services.source_estimation import (
    DEFAULT_LOOKBACK_HOURS,
    DEFAULT_STEP_HOURS,
    MODEL_VERSION as SOURCE_MODEL_VERSION,
    SourceEstimationError,
    generate_source_candidate_polygon,
    run_backward_drift,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ExperimentError(Exception):
    """Raised when the experiment pipeline fails."""


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass
class VesselFeatures:
    """Calculated features for one vessel candidate.  No hardcoded values."""
    vessel_id: str          # MMSI or constructed identifier
    vessel_name: str | None
    mmsi: str | None
    # Raw features
    min_source_distance_km: float | None   # Minimum distance between any AIS pos and source zone
    temporal_overlap_hours: float          # Hours the vessel was in the source time window
    trajectory_overlap_fraction: float     # Fraction of vessel positions inside source zone bbox
    heading_consistency: float | None      # Cosine similarity with backward drift direction
    speed_consistency: float | None        # Plausibility of vessel speed vs drift speed
    ais_position_count: int
    ais_coverage_fraction: float           # Positions available / max expected for time window
    # Derived score (feature-weighted, deterministic)
    evidence_consistency_score: float      # [0, 1]; higher = more consistent with evidence
    rank: int = 0
    has_meaningful_support: bool = True    # False if no spatial/temporal overlap at all

    def as_dict(self) -> dict[str, Any]:
        return {
            "vessel_id": self.vessel_id,
            "vessel_name": self.vessel_name,
            "mmsi": self.mmsi,
            "min_source_distance_km": self.min_source_distance_km,
            "temporal_overlap_hours": self.temporal_overlap_hours,
            "trajectory_overlap_fraction": self.trajectory_overlap_fraction,
            "heading_consistency": self.heading_consistency,
            "speed_consistency": self.speed_consistency,
            "ais_position_count": self.ais_position_count,
            "ais_coverage_fraction": self.ais_coverage_fraction,
            "evidence_consistency_score": self.evidence_consistency_score,
            "rank": self.rank,
            "has_meaningful_support": self.has_meaningful_support,
        }


@dataclass
class ExperimentResult:
    """Complete real-data attribution experiment result."""
    run_id: str
    satellite_product_id: str
    observation_time: datetime
    backtrack_hours: float
    step_hours: float
    model_version: str
    # Source reconstruction
    source_lon: float
    source_lat: float
    source_radius_m: float
    source_zone_geojson: dict[str, Any]
    backward_steps: list[dict[str, Any]]
    # Vessel attribution
    vessels: list[VesselFeatures]
    # Metadata
    era5_path: str
    cmems_path: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    scientific_disclaimer: str = (
        "Physics + feature-based attribution baseline. "
        "This is NOT a trained machine-learning model. "
        "Results represent probabilistic source attribution based on available evidence, "
        "NOT proof of legal responsibility or causation."
    )

    def __post_init__(self) -> None:
        if isinstance(self.observation_time, str):
            self.observation_time = datetime.fromisoformat(self.observation_time)
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "satellite_product_id": self.satellite_product_id,
            "observation_time": self.observation_time.isoformat(),
            "backtrack_hours": self.backtrack_hours,
            "step_hours": self.step_hours,
            "model_version": self.model_version,
            "source_lon": self.source_lon,
            "source_lat": self.source_lat,
            "source_radius_m": self.source_radius_m,
            "source_zone_geojson": self.source_zone_geojson,
            "backward_steps": self.backward_steps,
            "vessels": [v.as_dict() for v in self.vessels],
            "era5_path": self.era5_path,
            "cmems_path": self.cmems_path,
            "created_at": self.created_at.isoformat(),
            "scientific_disclaimer": self.scientific_disclaimer,
        }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

class ExperimentRunner:
    """Run a real-data attribution experiment end-to-end.

    Reuses existing Stage D3 backward drift engine directly.
    Does NOT touch the G1 investigation workflow or simulation code.
    """

    MODEL_VERSION = f"experiment_runner_v1+{SOURCE_MODEL_VERSION}"

    def run(
        self,
        *,
        run_id: str | None = None,
        satellite_product_id: str,
        observation_lon: float,
        observation_lat: float,
        observation_time: datetime,
        era5_netcdf_path: str,
        cmems_netcdf_path: str,
        backtrack_hours: float = DEFAULT_LOOKBACK_HOURS,
        step_hours: float = DEFAULT_STEP_HOURS,
        spill_area_m2: float | None = None,
        selected_vessels: list[dict[str, Any]] | None = None,
    ) -> ExperimentResult:
        """Execute the full real-data attribution experiment.

        Args:
            run_id:                 Optional existing run ID; auto-generated if None.
            satellite_product_id:   CDSE product identifier for provenance.
            observation_lon/lat:    Observed spill centroid (WGS84).
            observation_time:       Sentinel-1 scene acquisition timestamp (UTC).
            era5_netcdf_path:       Path to ERA5 10 m wind NetCDF acquired by EnvironmentSelectorService.
            cmems_netcdf_path:      Path to CMEMS surface current NetCDF acquired by EnvironmentSelectorService.
            backtrack_hours:        Duration of backward drift integration.
            step_hours:             Euler integration step size.
            spill_area_m2:          Observed slick area used for initial source radius.
            selected_vessels:       List of vessel dicts with 'mmsi', 'vessel_name', 'positions'
                                    (list of {timestamp, lat, lon, speed, heading}).

        Returns:
            ExperimentResult with source reconstruction and ranked vessel features.

        Raises:
            ExperimentError: if the backward drift engine or feature calculation fails.
        """
        if run_id is None:
            run_id = str(uuid.uuid4())

        obs_utc = _utc(observation_time)

        # Step 1 — Backward drift (reuses Stage D3 pure function)
        try:
            from app.services.drift_modelling import _open_netcdf
            wind_ds = _open_netcdf(era5_netcdf_path)
            curr_ds = _open_netcdf(cmems_netcdf_path)

            backward_steps = run_backward_drift(
                origin_lon=observation_lon,
                origin_lat=observation_lat,
                observation_time=obs_utc,
                wind_ds=wind_ds,
                curr_ds=curr_ds,
                lookback_hours=backtrack_hours,
                step_hours=step_hours,
                spill_area_m2=spill_area_m2,
            )
            wind_ds.close()
            curr_ds.close()
        except SourceEstimationError as exc:
            raise ExperimentError(f"Backward drift failed: {exc}") from exc
        except Exception as exc:
            raise ExperimentError(f"Unexpected error during backward drift: {exc}") from exc

        if not backward_steps:
            raise ExperimentError("Backward drift produced no steps; cannot estimate source zone.")

        # Step 2 — Final reconstructed source position (earliest step = estimated origin)
        final_step = backward_steps[-1]
        source_lon = final_step.lon
        source_lat = final_step.lat
        source_radius_m = final_step.uncertainty_radius_m

        # Build source zone GeoJSON polygon
        source_zone_geojson = generate_source_candidate_polygon(
            center_lon=source_lon,
            center_lat=source_lat,
            radius_m=source_radius_m,
        )

        # Step 3 — Score each selected vessel
        vessel_features: list[VesselFeatures] = []
        if selected_vessels:
            for vessel_data in selected_vessels:
                features = self._score_vessel(
                    vessel_data=vessel_data,
                    source_lon=source_lon,
                    source_lat=source_lat,
                    source_radius_m=source_radius_m,
                    observation_time=obs_utc,
                    backtrack_hours=backtrack_hours,
                    backward_steps=backward_steps,
                )
                vessel_features.append(features)

        # Step 4 — Rank by evidence consistency score
        vessel_features.sort(key=lambda f: f.evidence_consistency_score, reverse=True)
        for rank_idx, vf in enumerate(vessel_features, start=1):
            vf.rank = rank_idx

        return ExperimentResult(
            run_id=run_id,
            satellite_product_id=satellite_product_id,
            observation_time=obs_utc,
            backtrack_hours=backtrack_hours,
            step_hours=step_hours,
            model_version=self.MODEL_VERSION,
            source_lon=source_lon,
            source_lat=source_lat,
            source_radius_m=source_radius_m,
            source_zone_geojson=source_zone_geojson,
            backward_steps=[
                {
                    "step": i,
                    "lon": s.lon,
                    "lat": s.lat,
                    "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                    "uncertainty_radius_m": s.uncertainty_radius_m,
                }
                for i, s in enumerate(backward_steps)
            ],
            vessels=vessel_features,
            era5_path=era5_netcdf_path,
            cmems_path=cmems_netcdf_path,
        )

    # ------------------------------------------------------------------
    # Vessel feature calculation (all computed from actual input data)
    # ------------------------------------------------------------------

    def _score_vessel(
        self,
        *,
        vessel_data: dict[str, Any],
        source_lon: float,
        source_lat: float,
        source_radius_m: float,
        observation_time: datetime,
        backtrack_hours: float,
        backward_steps: list[Any],
    ) -> VesselFeatures:
        """Calculate evidence consistency features for one vessel.

        Returns low score (has_meaningful_support=False) if the vessel has no
        spatial or temporal overlap with the reconstructed source zone.
        """
        mmsi = vessel_data.get("mmsi")
        vessel_name = vessel_data.get("vessel_name")
        vessel_id = mmsi or vessel_name or vessel_data.get("id", str(uuid.uuid4()))
        positions = vessel_data.get("positions", [])

        if not positions:
            return VesselFeatures(
                vessel_id=vessel_id,
                vessel_name=vessel_name,
                mmsi=mmsi,
                min_source_distance_km=None,
                temporal_overlap_hours=0.0,
                trajectory_overlap_fraction=0.0,
                heading_consistency=None,
                speed_consistency=None,
                ais_position_count=0,
                ais_coverage_fraction=0.0,
                evidence_consistency_score=0.0,
                has_meaningful_support=False,
            )

        # Parse and sort positions
        parsed: list[dict[str, Any]] = []
        for pos in positions:
            try:
                ts = pos.get("timestamp")
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if ts and math.isfinite(pos.get("lat", float("nan"))) and math.isfinite(pos.get("lon", float("nan"))):
                    parsed.append({"t": _utc(ts), "lat": pos["lat"], "lon": pos["lon"],
                                   "speed": pos.get("speed"), "heading": pos.get("heading")})
            except Exception:
                pass
        parsed.sort(key=lambda p: p["t"])

        if not parsed:
            return VesselFeatures(
                vessel_id=vessel_id, vessel_name=vessel_name, mmsi=mmsi,
                min_source_distance_km=None, temporal_overlap_hours=0.0,
                trajectory_overlap_fraction=0.0, heading_consistency=None,
                speed_consistency=None, ais_position_count=0, ais_coverage_fraction=0.0,
                evidence_consistency_score=0.0, has_meaningful_support=False,
            )

        # Time window of the backward backtrack
        window_start = observation_time - __import__("datetime").timedelta(hours=backtrack_hours)
        window_end = observation_time

        # Filter positions to the relevant time window
        window_positions = [p for p in parsed if window_start <= p["t"] <= window_end]

        # --- Feature 1: Minimum distance to reconstructed source point ---
        min_dist_km: float | None = None
        for pos in window_positions or parsed:
            d = _haversine_km(pos["lat"], pos["lon"], source_lat, source_lon)
            if min_dist_km is None or d < min_dist_km:
                min_dist_km = d

        # --- Feature 2: Temporal overlap (hours in window) ---
        if window_positions:
            t_first = window_positions[0]["t"]
            t_last = window_positions[-1]["t"]
            temporal_overlap_h = (t_last - t_first).total_seconds() / 3600.0
        else:
            temporal_overlap_h = 0.0

        # --- Feature 3: Trajectory overlap fraction (positions inside source radius) ---
        source_radius_km = source_radius_m / 1000.0
        if window_positions:
            inside_count = sum(
                1 for p in window_positions
                if _haversine_km(p["lat"], p["lon"], source_lat, source_lon) <= source_radius_km
            )
            traj_overlap = inside_count / len(window_positions)
        else:
            traj_overlap = 0.0

        # --- Feature 4: AIS coverage fraction ---
        # Estimate expected positions based on typical AIS reporting rate (~6 min)
        expected_per_hour = 10.0
        expected_total = max(backtrack_hours * expected_per_hour, 1.0)
        ais_coverage = min(len(window_positions) / expected_total, 1.0)

        # --- Feature 5: Heading consistency ---
        heading_consistency: float | None = None
        if len(backward_steps) >= 2 and window_positions and window_positions[0].get("heading") is not None:
            # Drift direction at observation time
            first_step = backward_steps[0]
            drift_dir_deg = math.degrees(math.atan2(
                first_step.lon - source_lon,
                first_step.lat - source_lat,
            )) % 360
            vessel_heading = float(window_positions[0]["heading"])
            angle_diff = abs(vessel_heading - drift_dir_deg) % 360
            if angle_diff > 180:
                angle_diff = 360 - angle_diff
            # 1.0 = perfectly aligned, 0.0 = perfectly opposite
            heading_consistency = 1.0 - (angle_diff / 180.0)

        # --- Feature 6: Speed consistency ---
        speed_consistency: float | None = None
        if window_positions:
            speeds = [p["speed"] for p in window_positions if p.get("speed") is not None]
            if speeds:
                avg_speed_knots = sum(speeds) / len(speeds)
                # A vessel very close to the source zone would typically be moving <5 knots
                # if stationary (discharging). Penalise very high speeds during window.
                if avg_speed_knots <= 1.0:
                    speed_consistency = 1.0
                elif avg_speed_knots <= 8.0:
                    speed_consistency = 1.0 - (avg_speed_knots - 1.0) / 20.0
                else:
                    speed_consistency = max(0.0, 1.0 - avg_speed_knots / 20.0)

        # --- Composite evidence consistency score ---
        # Spatial proximity: primary signal (weight 0.50)
        # Temporal overlap:  secondary (weight 0.25)
        # Trajectory overlap: secondary (weight 0.25)
        # Missing signals excluded from denominator (never treated as zero)
        score = _compute_score(
            min_dist_km=min_dist_km,
            source_radius_km=source_radius_km,
            temporal_overlap_h=temporal_overlap_h,
            backtrack_hours=backtrack_hours,
            traj_overlap=traj_overlap,
        )

        has_support = (
            traj_overlap > 0.0
            or (min_dist_km is not None and min_dist_km < source_radius_km * 3)
            or temporal_overlap_h > 0.0
        )

        return VesselFeatures(
            vessel_id=vessel_id,
            vessel_name=vessel_name,
            mmsi=mmsi,
            min_source_distance_km=round(min_dist_km, 3) if min_dist_km is not None else None,
            temporal_overlap_hours=round(temporal_overlap_h, 3),
            trajectory_overlap_fraction=round(traj_overlap, 4),
            heading_consistency=round(heading_consistency, 4) if heading_consistency is not None else None,
            speed_consistency=round(speed_consistency, 4) if speed_consistency is not None else None,
            ais_position_count=len(window_positions),
            ais_coverage_fraction=round(ais_coverage, 4),
            evidence_consistency_score=round(score, 4),
            has_meaningful_support=has_support,
        )


# ---------------------------------------------------------------------------
# Pure helper functions (testable, no side effects)
# ---------------------------------------------------------------------------

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _compute_score(
    *,
    min_dist_km: float | None,
    source_radius_km: float,
    temporal_overlap_h: float,
    backtrack_hours: float,
    traj_overlap: float,
) -> float:
    """Compute deterministic evidence consistency score in [0, 1].

    Weights:
        spatial_score:  0.50
        temporal_score: 0.25
        traj_score:     0.25
    Missing primary signal (min_dist_km=None) reduces the spatial weight to 0.
    """
    weights: dict[str, float] = {}
    values: dict[str, float] = {}

    # Spatial proximity score: 1.0 at source, decays linearly over 5x radius
    if min_dist_km is not None:
        max_dist_km = max(source_radius_km * 5, 1.0)
        spatial = max(0.0, 1.0 - min_dist_km / max_dist_km)
        weights["spatial"] = 0.50
        values["spatial"] = spatial

    # Temporal overlap score
    max_t = max(backtrack_hours, 1.0)
    temporal = min(temporal_overlap_h / max_t, 1.0)
    weights["temporal"] = 0.25
    values["temporal"] = temporal

    # Trajectory overlap score (already in [0, 1])
    weights["traj"] = 0.25
    values["traj"] = traj_overlap

    total_weight = sum(weights.values())
    if total_weight <= 0.0:
        return 0.0

    raw = sum(values[k] * weights[k] for k in values) / total_weight
    return min(1.0, max(0.0, raw))


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
