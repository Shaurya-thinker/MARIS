"""MARIS Phase #5 — Monte Carlo Ensemble & Uncertainty Propagation Service.

Implements a dedicated Monte Carlo uncertainty layer wrapping the existing deterministic
Stage D3 backward drift engine (run_backward_drift) without modifying or replacing it.

Scientific Framing & Operational Principles:
1. REPRODUCIBILITY: Uses a local numpy.random.default_rng(seed) to guarantee identical
   results given identical inputs and seed.
2. NON-DESTRUCTIVE: The deterministic baseline is executed first and preserved 100% intact.
3. DATASET SAFETY: ERA5 and CMEMS xarray Datasets are never mutated in-place; perturbations
   are applied in-memory through lightweight realization-specific xarray Datasets.
4. STRICT PROVENANCE: Every realization is explicitly tagged as
   'MODEL_GENERATED_MONTE_CARLO_REALIZATION'. Never labeled as AIS or SAR observations.
5. ZERO-FABRICATION: Trajectories are model-generated uncertainty realizations.
   Ensemble support frequency is a physical sensitivity metric, NOT a probability of legal guilt.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import xarray as xr

from app.services.source_estimation import (
    DEFAULT_LOOKBACK_HOURS,
    DEFAULT_STEP_HOURS,
    run_backward_drift,
)

PROVENANCE_MONTE_CARLO = "MODEL_GENERATED_MONTE_CARLO_REALIZATION"
DEFAULT_DISCLAIMER = (
    "Monte Carlo ensemble trajectories represent model-generated uncertainty realizations under "
    "configured physical parameter perturbations. They are NOT observed vessel tracks or satellite "
    "observations, and ensemble frequencies do NOT constitute a probability of legal responsibility."
)


# ---------------------------------------------------------------------------
# Configuration DTO
# ---------------------------------------------------------------------------

@dataclass
class MonteCarloConfig:
    """Configuration parameters for Monte Carlo drift ensemble execution."""
    enabled: bool = False
    ensemble_size: int = 50
    seed: int | None = None
    perturb_origin: bool = True
    origin_std_m: float = 1000.0
    perturb_leeway: bool = True
    leeway_std: float = 0.005
    perturb_wind: bool = True
    wind_speed_std_ms: float = 1.0
    wind_dir_std_deg: float = 10.0
    perturb_current: bool = True
    current_std_ms: float = 0.05

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> MonteCarloConfig:
        if not data:
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            ensemble_size=int(np.clip(data.get("ensemble_size", 50), 10, 500)),
            seed=int(data["seed"]) if data.get("seed") is not None else None,
            perturb_origin=bool(data.get("perturb_origin", True)),
            origin_std_m=float(data.get("origin_std_m", 1000.0)),
            perturb_leeway=bool(data.get("perturb_leeway", True)),
            leeway_std=float(data.get("leeway_std", 0.005)),
            perturb_wind=bool(data.get("perturb_wind", True)),
            wind_speed_std_ms=float(data.get("wind_speed_std_ms", 1.0)),
            wind_dir_std_deg=float(data.get("wind_dir_std_deg", 10.0)),
            perturb_current=bool(data.get("perturb_current", True)),
            current_std_ms=float(data.get("current_std_ms", 0.05)),
        )


# ---------------------------------------------------------------------------
# Result DTOs
# ---------------------------------------------------------------------------

@dataclass
class EnsembleRealization:
    """One individual Monte Carlo trajectory realization."""
    realization_id: int
    steps: list[dict[str, Any]]
    final_source_lon: float
    final_source_lat: float
    final_uncertainty_radius_m: float
    perturbation_parameters: dict[str, float]
    termination_status: str = "completed"
    provenance: str = PROVENANCE_MONTE_CARLO

    def as_dict(self) -> dict[str, Any]:
        return {
            "realization_id": self.realization_id,
            "steps": self.steps,
            "final_source_lon": round(self.final_source_lon, 6),
            "final_source_lat": round(self.final_source_lat, 6),
            "final_uncertainty_radius_m": round(self.final_uncertainty_radius_m, 2),
            "perturbation_parameters": self.perturbation_parameters,
            "termination_status": self.termination_status,
            "provenance": self.provenance,
        }


@dataclass
class EnsembleEvidence:
    """Vessel-level sensitivity metrics across the Monte Carlo ensemble."""
    ensemble_support_fraction: float
    trajectory_consistency_across_ensemble: float | None = None
    source_intersection_fraction: float | None = None
    score_mean: float | None = None
    score_std: float | None = None
    score_p05: float | None = None
    score_p95: float | None = None
    ensemble_disclaimer: str = (
        "Ensemble support represents the fraction of configured model realizations exhibiting "
        "spatiotemporal correlation under perturbed forcing. It is a sensitivity measure, "
        "not a probability of causation or legal liability."
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ensemble_support_fraction": round(self.ensemble_support_fraction, 4),
            "trajectory_consistency_across_ensemble": (
                round(self.trajectory_consistency_across_ensemble, 4)
                if self.trajectory_consistency_across_ensemble is not None
                else None
            ),
            "source_intersection_fraction": (
                round(self.source_intersection_fraction, 4)
                if self.source_intersection_fraction is not None
                else None
            ),
            "score_mean": round(self.score_mean, 4) if self.score_mean is not None else None,
            "score_std": round(self.score_std, 4) if self.score_std is not None else None,
            "score_p05": round(self.score_p05, 4) if self.score_p05 is not None else None,
            "score_p95": round(self.score_p95, 4) if self.score_p95 is not None else None,
            "ensemble_disclaimer": self.ensemble_disclaimer,
        }


@dataclass
class MonteCarloEnsembleResult:
    """Aggregate Monte Carlo ensemble output structure."""
    ensemble_size: int
    realizations: list[EnsembleRealization]
    mean_trajectory: list[dict[str, Any]]
    final_source_centroid: dict[str, float]
    dispersion_radius_km: float
    p05_source_lon: float
    p95_source_lon: float
    p05_source_lat: float
    p95_source_lat: float
    effective_seed: int | None
    execution_time_ms: float
    provenance: str = PROVENANCE_MONTE_CARLO
    scientific_disclaimer: str = DEFAULT_DISCLAIMER

    def as_dict(self) -> dict[str, Any]:
        return {
            "ensemble_size": self.ensemble_size,
            "realizations": [r.as_dict() for r in self.realizations],
            "mean_trajectory": self.mean_trajectory,
            "final_source_centroid": {
                "lon": round(self.final_source_centroid["lon"], 6),
                "lat": round(self.final_source_centroid["lat"], 6),
            },
            "dispersion_radius_km": round(self.dispersion_radius_km, 3),
            "p05_source_lon": round(self.p05_source_lon, 6),
            "p95_source_lon": round(self.p95_source_lon, 6),
            "p05_source_lat": round(self.p05_source_lat, 6),
            "p95_source_lat": round(self.p95_source_lat, 6),
            "effective_seed": self.effective_seed,
            "execution_time_ms": round(self.execution_time_ms, 2),
            "provenance": self.provenance,
            "scientific_disclaimer": self.scientific_disclaimer,
        }


# ---------------------------------------------------------------------------
# Helper Geometry Functions
# ---------------------------------------------------------------------------

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(min(a, 1.0)))


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class MonteCarloEnsembleService:
    """Monte Carlo uncertainty propagation service wrapping the pure Stage D3 drift engine."""

    def run_ensemble(
        self,
        *,
        config: MonteCarloConfig,
        origin_lon: float,
        origin_lat: float,
        observation_time: datetime,
        wind_ds: xr.Dataset,
        curr_ds: xr.Dataset,
        lookback_hours: float = DEFAULT_LOOKBACK_HOURS,
        step_hours: float = DEFAULT_STEP_HOURS,
        spill_area_m2: float | None = None,
        domain_checker: Any | None = None,
    ) -> MonteCarloEnsembleResult:
        """Execute N Monte Carlo backward drift realizations with parameter perturbation.

        Strictly preserves the underlying run_backward_drift function.
        Never mutates the input wind_ds or curr_ds xarray Datasets in-place.
        """
        start_t = time.perf_counter()
        rng = np.random.default_rng(config.seed)

        # Pre-cache original numpy arrays from wind and current datasets to avoid repeated disk reads
        u10_orig = wind_ds["u10"].values
        v10_orig = wind_ds["v10"].values
        uo_orig = curr_ds["uo"].values
        vo_orig = curr_ds["vo"].values

        realizations: list[EnsembleRealization] = []
        final_lons: list[float] = []
        final_lats: list[float] = []

        cos_lat = max(math.cos(math.radians(origin_lat)), 0.1)

        for i in range(config.ensemble_size):
            # 1. Perturb Initial Origin Position
            if config.perturb_origin and config.origin_std_m > 0:
                dx_m = float(np.clip(rng.normal(0.0, config.origin_std_m), -2.5 * config.origin_std_m, 2.5 * config.origin_std_m))
                dy_m = float(np.clip(rng.normal(0.0, config.origin_std_m), -2.5 * config.origin_std_m, 2.5 * config.origin_std_m))
                r_lon = origin_lon + dx_m / (cos_lat * 111320.0)
                r_lat = origin_lat + dy_m / 111320.0
                if domain_checker and not domain_checker.is_maritime(r_lon, r_lat):
                    # Land collision fallback: keep baseline origin
                    r_lon = origin_lon
                    r_lat = origin_lat
                    dx_m, dy_m = 0.0, 0.0
            else:
                r_lon, r_lat = origin_lon, origin_lat
                dx_m, dy_m = 0.0, 0.0

            # 2. Perturb Leeway Fraction (clamped strictly to [0.01, 0.05])
            if config.perturb_leeway and config.leeway_std > 0:
                r_leeway = float(np.clip(rng.normal(0.035, config.leeway_std), 0.01, 0.05))
            else:
                r_leeway = 0.035

            # 3. Perturb Wind (Velocity & Direction)
            delta_wind_speed = 0.0
            delta_wind_dir = 0.0
            if config.perturb_wind and (config.wind_speed_std_ms > 0 or config.wind_dir_std_deg > 0):
                delta_wind_dir = float(np.clip(rng.normal(0.0, config.wind_dir_std_deg), -3.0 * config.wind_dir_std_deg, 3.0 * config.wind_dir_std_deg))
                delta_wind_speed = float(np.clip(rng.normal(0.0, config.wind_speed_std_ms), -3.0 * config.wind_speed_std_ms, 3.0 * config.wind_speed_std_ms))

                # Rotate wind vector
                d_theta = math.radians(delta_wind_dir)
                cos_w = math.cos(d_theta)
                sin_w = math.sin(d_theta)
                u_rot = u10_orig * cos_w - v10_orig * sin_w
                v_rot = u10_orig * sin_w + v10_orig * cos_w

                # Scale speed
                spd = np.hypot(u_rot, v_rot)
                scale = np.where(spd > 1e-4, np.clip(1.0 + delta_wind_speed / np.maximum(spd, 0.5), 0.2, 3.0), 1.0)
                u_pert = u_rot * scale
                v_pert = v_rot * scale

                r_wind_ds = xr.Dataset(
                    data_vars={
                        "u10": (wind_ds["u10"].dims, u_pert),
                        "v10": (wind_ds["v10"].dims, v_pert),
                    },
                    coords=wind_ds.coords,
                )
            else:
                r_wind_ds = wind_ds

            # 4. Perturb Current (Near-surface uo, vo)
            delta_curr_u = 0.0
            delta_curr_v = 0.0
            if config.perturb_current and config.current_std_ms > 0:
                delta_curr_u = float(np.clip(rng.normal(0.0, config.current_std_ms), -3.0 * config.current_std_ms, 3.0 * config.current_std_ms))
                delta_curr_v = float(np.clip(rng.normal(0.0, config.current_std_ms), -3.0 * config.current_std_ms, 3.0 * config.current_std_ms))

                uo_pert = uo_orig + delta_curr_u
                vo_pert = vo_orig + delta_curr_v

                r_curr_ds = xr.Dataset(
                    data_vars={
                        "uo": (curr_ds["uo"].dims, uo_pert),
                        "vo": (curr_ds["vo"].dims, vo_pert),
                    },
                    coords=curr_ds.coords,
                )
            else:
                r_curr_ds = curr_ds

            # 5. Execute Pure Backward Drift
            traj = run_backward_drift(
                origin_lon=r_lon,
                origin_lat=r_lat,
                observation_time=observation_time,
                wind_ds=r_wind_ds,
                curr_ds=r_curr_ds,
                lookback_hours=lookback_hours,
                step_hours=step_hours,
                leeway_fraction=r_leeway,
                spill_area_m2=spill_area_m2,
                domain_checker=domain_checker,
            )

            if not traj:
                continue

            earliest_step = traj[-1]
            final_lons.append(earliest_step.lon)
            final_lats.append(earliest_step.lat)

            serialized_steps = [
                {
                    "step": idx + 1,
                    "lon": s.lon,
                    "lat": s.lat,
                    "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                    "uncertainty_radius_m": s.uncertainty_radius_m,
                    "drift_u_ms": s.drift_u_ms,
                    "drift_v_ms": s.drift_v_ms,
                }
                for idx, s in enumerate(traj)
            ]

            realizations.append(
                EnsembleRealization(
                    realization_id=i + 1,
                    steps=serialized_steps,
                    final_source_lon=earliest_step.lon,
                    final_source_lat=earliest_step.lat,
                    final_uncertainty_radius_m=earliest_step.uncertainty_radius_m,
                    perturbation_parameters={
                        "dx_m": round(dx_m, 2),
                        "dy_m": round(dy_m, 2),
                        "leeway_factor": round(r_leeway, 5),
                        "leeway_fraction": round(r_leeway, 5),
                        "delta_wind_speed_ms": round(delta_wind_speed, 3),
                        "wind_speed_delta_ms": round(delta_wind_speed, 3),
                        "delta_wind_dir_deg": round(delta_wind_dir, 2),
                        "wind_dir_delta_deg": round(delta_wind_dir, 2),
                        "delta_curr_u_ms": round(delta_curr_u, 4),
                        "delta_curr_v_ms": round(delta_curr_v, 4),
                    },
                    termination_status=getattr(traj, "termination_status", "completed"),
                )
            )

        elapsed_ms = (time.perf_counter() - start_t) * 1000.0

        if not final_lons:
            # Fallback if all realizations failed
            final_lons = [origin_lon]
            final_lats = [origin_lat]

        centroid_lon = float(np.mean(final_lons))
        centroid_lat = float(np.mean(final_lats))

        # Dispersion radius: 95th percentile distance from centroid to final source points in km
        distances = [_haversine_km(centroid_lat, centroid_lon, lat, lon) for lon, lat in zip(final_lons, final_lats)]
        dispersion_km = float(np.percentile(distances, 95)) if distances else 0.0

        # Mean trajectory
        max_steps = max(len(r.steps) for r in realizations) if realizations else 0
        mean_steps: list[dict[str, Any]] = []
        for step_idx in range(max_steps):
            step_lons = [r.steps[step_idx]["lon"] for r in realizations if step_idx < len(r.steps)]
            step_lats = [r.steps[step_idx]["lat"] for r in realizations if step_idx < len(r.steps)]
            step_ts = next((r.steps[step_idx]["timestamp"] for r in realizations if step_idx < len(r.steps)), None)
            if step_lons and step_lats:
                mean_steps.append({
                    "step": step_idx + 1,
                    "lon": round(float(np.mean(step_lons)), 6),
                    "lat": round(float(np.mean(step_lats)), 6),
                    "timestamp": step_ts,
                })

        return MonteCarloEnsembleResult(
            ensemble_size=len(realizations),
            realizations=realizations,
            mean_trajectory=mean_steps,
            final_source_centroid={"lon": centroid_lon, "lat": centroid_lat},
            dispersion_radius_km=dispersion_km,
            p05_source_lon=float(np.percentile(final_lons, 5)),
            p95_source_lon=float(np.percentile(final_lons, 95)),
            p05_source_lat=float(np.percentile(final_lats, 5)),
            p95_source_lat=float(np.percentile(final_lats, 95)),
            effective_seed=config.seed,
            execution_time_ms=elapsed_ms,
        )

    @staticmethod
    def evaluate_vessel_ensemble_evidence(
        *,
        vessel_positions: list[dict[str, Any]],
        ensemble: MonteCarloEnsembleResult,
        observation_time: datetime,
        backtrack_hours: float,
        deterministic_score: float,
    ) -> EnsembleEvidence:
        """Compute vessel attribution sensitivity across the Monte Carlo ensemble.

        ZERO-FABRICATION: Evaluates ONLY authentic recorded positions. Never interpolates.
        """
        if not ensemble.realizations or not vessel_positions:
            return EnsembleEvidence(
                ensemble_support_fraction=0.0,
                trajectory_consistency_across_ensemble=0.0,
                score_mean=deterministic_score,
                score_std=0.0,
                score_p05=deterministic_score,
                score_p95=deterministic_score,
            )

        t_source = observation_time.timestamp() - (backtrack_hours * 3600.0)
        source_tolerance_s = 1.5 * 3600.0  # +/- 1.5h

        support_count = 0
        traj_near_count = 0
        realization_scores: list[float] = []

        parsed_positions: list[tuple[float, float, float]] = []
        for p in vessel_positions:
            t_raw = p.get("timestamp") or p.get("t")
            lat = p.get("lat")
            lon = p.get("lon")
            if lat is not None and lon is not None and t_raw is not None:
                if isinstance(t_raw, datetime):
                    t_val = t_raw.timestamp()
                elif isinstance(t_raw, str):
                    try:
                        t_val = datetime.fromisoformat(t_raw.replace("Z", "+00:00")).timestamp()
                    except Exception:
                        continue
                else:
                    continue
                parsed_positions.append((float(lat), float(lon), t_val))

        if not parsed_positions:
            return EnsembleEvidence(
                ensemble_support_fraction=0.0,
                trajectory_consistency_across_ensemble=0.0,
                score_mean=deterministic_score,
                score_std=0.0,
                score_p05=deterministic_score,
                score_p95=deterministic_score,
            )

        source_intersect_count = 0
        traj_near_count = 0
        support_count = 0
        realization_scores: list[float] = []

        for r in ensemble.realizations:
            r_src_lon = r.final_source_lon
            r_src_lat = r.final_source_lat
            r_src_radius_km = r.final_uncertainty_radius_m / 1000.0

            # 1. Source Zone Intersection for this realization
            has_source_intersect = False
            for lat, lon, t_val in parsed_positions:
                if abs(t_val - t_source) <= source_tolerance_s:
                    if _haversine_km(lat, lon, r_src_lat, r_src_lon) <= r_src_radius_km:
                        has_source_intersect = True
                        break

            if has_source_intersect:
                source_intersect_count += 1

            # 2. Min Distance to this realization's trajectory corridor
            min_r_traj_dist = min(
                (
                    _haversine_km(p_lat, p_lon, step["lat"], step["lon"])
                    for p_lat, p_lon, _ in parsed_positions
                    for step in r.steps
                ),
                default=999.0,
            )

            # Corridors expand with uncertainty radius
            corridor_thresh_km = max(r_src_radius_km, 5.0)
            is_corridor_consistent = min_r_traj_dist <= corridor_thresh_km
            if is_corridor_consistent:
                traj_near_count += 1

            # 3. Spatial Proximity Score under this realization's source
            min_r_src_dist = min(
                _haversine_km(p_lat, p_lon, r_src_lat, r_src_lon)
                for p_lat, p_lon, _ in parsed_positions
            )
            # Reconstruct spatial proxy score
            if min_r_src_dist <= r_src_radius_km:
                r_spatial = 1.0
            elif min_r_src_dist <= r_src_radius_km * 2.0:
                r_spatial = max(0.0, 1.0 - (min_r_src_dist - r_src_radius_km) / r_src_radius_km)
            else:
                r_spatial = max(0.0, 1.0 - min_r_src_dist / (r_src_radius_km * 4.0))

            # Perturbed score variation around deterministic score
            r_score = np.clip(deterministic_score + 0.50 * (r_spatial - 1.0 if not has_source_intersect else 0.0), 0.0, 1.0)
            realization_scores.append(float(r_score))

            # A realization supports attribution if:
            # 1) authentic AIS intersects source zone, OR
            # 2) authentic AIS track passes within drift corridor, OR
            # 3) realization score maintains evidence consistency (r_score >= 0.50)
            if has_source_intersect or is_corridor_consistent or r_score >= 0.50:
                support_count += 1

        n = len(ensemble.realizations)
        support_fraction = support_count / n if n > 0 else 0.0
        traj_consistency = traj_near_count / n if n > 0 else 0.0
        src_fraction = source_intersect_count / n if n > 0 else 0.0

        return EnsembleEvidence(
            ensemble_support_fraction=support_fraction,
            trajectory_consistency_across_ensemble=traj_consistency,
            source_intersection_fraction=src_fraction,
            score_mean=float(np.mean(realization_scores)),
            score_std=float(np.std(realization_scores)),
            score_p05=float(np.percentile(realization_scores, 5)),
            score_p95=float(np.percentile(realization_scores, 95)),
        )
