"""Synthetic scenario generator for MARIS attribution experiments.

Generates physically consistent synthetic marine oil spill scenarios:
- Calibrated Sentinel-1 SAR observation metadata and slick origin
- Calibrated ERA5-like 10m wind field (u10, v10)
- Calibrated CMEMS-like ocean surface current field (uo, vo)
- 1 Ground-Truth vessel whose trajectory passes through the spill release zone at release time
- N-1 Distractor vessels with realistic maritime traffic trajectories
- Seeded pseudo-randomness for deterministic reproducibility
"""

from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import xarray as xr


@dataclass
class SyntheticScenario:
    """Complete specification of a synthetic oil spill investigation scenario."""

    scenario_id: str
    seed: int
    observation_time: datetime
    backtrack_hours: float
    step_hours: float
    origin_lon: float
    origin_lat: float
    spill_area_m2: float
    wind_speed_ms: float
    wind_direction_deg: float
    current_speed_ms: float
    current_direction_deg: float
    u10: float
    v10: float
    uo: float
    vo: float
    ground_truth_vessel_id: str
    vessels: list[dict[str, Any]]
    estimated_source_lon: float
    estimated_source_lat: float
    wind_ds: xr.Dataset = field(repr=False)
    curr_ds: xr.Dataset = field(repr=False)
    is_synthetic: bool = True

    def to_summary_dict(self) -> dict[str, Any]:
        """Convert scenario to serializable dictionary (omitting large datasets)."""
        return {
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "observation_time": self.observation_time.isoformat(),
            "backtrack_hours": self.backtrack_hours,
            "step_hours": self.step_hours,
            "origin_lon": round(self.origin_lon, 5),
            "origin_lat": round(self.origin_lat, 5),
            "spill_area_m2": round(self.spill_area_m2, 1),
            "wind_speed_ms": round(self.wind_speed_ms, 2),
            "wind_direction_deg": round(self.wind_direction_deg, 1),
            "current_speed_ms": round(self.current_speed_ms, 3),
            "current_direction_deg": round(self.current_direction_deg, 1),
            "ground_truth_vessel_id": self.ground_truth_vessel_id,
            "estimated_source_lon": round(self.estimated_source_lon, 5),
            "estimated_source_lat": round(self.estimated_source_lat, 5),
            "candidate_count": len(self.vessels),
            "vessels": self.vessels,
            "is_synthetic": True,
        }


def _speed_dir_to_uv(speed: float, direction_deg: float) -> tuple[float, float]:
    """Convert speed (m/s) and direction (deg from North clockwise) to eastward (u) and northward (v)."""
    rad = math.radians(direction_deg)
    u = speed * math.sin(rad)
    v = speed * math.cos(rad)
    return u, v


def generate_synthetic_scenario(
    seed: int | None = None,
    origin_lat: float | None = None,
    origin_lon: float | None = None,
    observation_time: datetime | None = None,
    wind_speed_ms: float | None = None,
    wind_direction_deg: float | None = None,
    current_speed_ms: float | None = None,
    current_direction_deg: float | None = None,
    candidate_count: int = 4,
    backtrack_hours: float = 6.0,
    step_hours: float = 0.5,
    spill_area_m2: float | None = None,
) -> SyntheticScenario:
    """Generate a physically consistent synthetic investigation scenario."""
    if seed is None:
        seed = random.randint(100000, 999999)

    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)

    scenario_id = f"syn_{seed}_{uuid.uuid4().hex[:6]}"

    # 1. Spatial Origin (Satellite observation centroid)
    # Default within Ligurian Sea / Mediterranean corridor (42.5°N - 44.0°N, 8.0°E - 10.5°E)
    if origin_lat is None:
        origin_lat = 43.0 + rng.uniform(-0.5, 0.5)
    if origin_lon is None:
        origin_lon = 9.5 + rng.uniform(-0.5, 0.5)

    # 2. Observation Time (UTC)
    if observation_time is None:
        base_date = datetime(2024, 6, 15, 8, 0, tzinfo=timezone.utc)
        time_offset_days = rng.randint(0, 60)
        time_offset_hours = rng.randint(0, 23)
        observation_time = base_date + timedelta(days=time_offset_days, hours=time_offset_hours)
    elif observation_time.tzinfo is None:
        observation_time = observation_time.replace(tzinfo=timezone.utc)

    # 3. Environmental Conditions (calibrated physical ranges)
    if wind_speed_ms is None:
        wind_speed_ms = rng.uniform(2.5, 12.0)  # Gentle breeze to strong breeze (Beaufort 2-6)
    if wind_direction_deg is None:
        wind_direction_deg = rng.uniform(0.0, 360.0)

    if current_speed_ms is None:
        current_speed_ms = rng.uniform(0.05, 0.45)  # Typical Mediterranean surface current (0.1 - 0.9 knots)
    if current_direction_deg is None:
        # Prevailing current correlated with or independent of wind
        current_direction_deg = (wind_direction_deg + rng.uniform(-45.0, 45.0)) % 360.0

    if spill_area_m2 is None:
        spill_area_m2 = rng.uniform(40000.0, 350000.0)

    u10, v10 = _speed_dir_to_uv(wind_speed_ms, wind_direction_deg)
    uo, vo = _speed_dir_to_uv(current_speed_ms, current_direction_deg)

    # 4. Physical Drift Vector & Source Zone
    # Standard leeway factor: leeway = 0.03 * wind
    drift_u = uo + 0.03 * u10
    drift_v = vo + 0.03 * v10

    # Source location at t_rel = t_obs - backtrack_hours
    # dt_seconds = backtrack_hours * 3600
    # delta_lon = (drift_u * dt_s) / (111320 * cos(lat_rad))
    # delta_lat = (drift_v * dt_s) / 111320
    dt_s = backtrack_hours * 3600.0
    lat_rad = math.radians(origin_lat)
    delta_lon = (drift_u * dt_s) / (111320.0 * max(math.cos(lat_rad), 0.1))
    delta_lat = (drift_v * dt_s) / 111320.0

    # Origin at t_obs resulted from drift starting at source: source = origin - delta
    source_lon = origin_lon - delta_lon
    source_lat = origin_lat - delta_lat
    release_time = observation_time - timedelta(hours=backtrack_hours)

    # 5. Build Environment NetCDF Datasets (xr.Dataset)
    # Span grid wide enough to encompass origin, source, and candidate vessels
    margin_deg = 2.0
    min_lat = min(origin_lat, source_lat) - margin_deg
    max_lat = max(origin_lat, source_lat) + margin_deg
    min_lon = min(origin_lon, source_lon) - margin_deg
    max_lon = max(origin_lon, source_lon) + margin_deg

    grid_lats = np.linspace(min_lat, max_lat, 15)
    grid_lons = np.linspace(min_lon, max_lon, 15)

    # Time grid spanning [obs - backtrack - 3h, obs + 2h]
    t_start = observation_time - timedelta(hours=backtrack_hours + 3)
    t_end = observation_time + timedelta(hours=2)
    step_count = int((t_end - t_start).total_seconds() / 3600) + 1
    time_series = [t_start + timedelta(hours=i) for i in range(step_count)]
    time_arr = np.array([np.datetime64(t.replace(tzinfo=None)) for t in time_series], dtype="datetime64[s]")

    shape = (len(time_arr), len(grid_lats), len(grid_lons))

    # Add minor spatial/temporal gradient to ensure realistic non-constant field
    grad_y = np.linspace(-0.05, 0.05, len(grid_lats))
    grad_x = np.linspace(-0.05, 0.05, len(grid_lons))
    mesh_gy, mesh_gx = np.meshgrid(grad_y, grad_x, indexing="ij")

    u10_grid = np.zeros(shape, dtype=np.float32)
    v10_grid = np.zeros(shape, dtype=np.float32)
    uo_grid = np.zeros(shape, dtype=np.float32)
    vo_grid = np.zeros(shape, dtype=np.float32)

    for ti in range(len(time_arr)):
        t_factor = 1.0 + 0.02 * math.sin(ti * 0.5)
        u10_grid[ti, :, :] = (u10 + mesh_gx) * t_factor
        v10_grid[ti, :, :] = (v10 + mesh_gy) * t_factor
        uo_grid[ti, :, :] = (uo + mesh_gx * 0.1) * t_factor
        vo_grid[ti, :, :] = (vo + mesh_gy * 0.1) * t_factor

    wind_ds = xr.Dataset(
        {
            "u10": (["time", "latitude", "longitude"], u10_grid),
            "v10": (["time", "latitude", "longitude"], v10_grid),
        },
        coords={
            "time": time_arr,
            "latitude": grid_lats.astype(np.float64),
            "longitude": grid_lons.astype(np.float64),
        },
    )

    curr_ds = xr.Dataset(
        {
            "uo": (["time", "latitude", "longitude"], uo_grid),
            "vo": (["time", "latitude", "longitude"], vo_grid),
        },
        coords={
            "time": time_arr,
            "latitude": grid_lats.astype(np.float64),
            "longitude": grid_lons.astype(np.float64),
        },
    )

    # 6. Generate Vessel Candidates
    # 1 Ground Truth vessel + (candidate_count - 1) Distractors
    vessels = []
    candidate_count = max(2, min(candidate_count, 10))

    # Ground truth vessel metadata
    gt_mmsi = str(rng.randint(200000000, 299999999))
    gt_name = f"VESSEL-{rng.choice(['ALTAIR', 'NEPTUNE', 'OCEANIC', 'MERIDIAN', 'AEGEAN'])}-{rng.randint(10, 99)}"
    gt_vessel_id = gt_mmsi

    # Ground truth trajectory passes very close to (source_lon, source_lat) at release_time
    # Realistic cargo/tanker navigation course
    gt_course_deg = rng.uniform(0.0, 360.0)
    gt_speed_knots = rng.uniform(10.0, 15.0)  # Typical cruising speed
    gt_speed_ms = gt_speed_knots * 0.514444

    # Distance covered per second
    gt_course_rad = math.radians(gt_course_deg)
    gt_v_north_ms = gt_speed_ms * math.cos(gt_course_rad)
    gt_v_east_ms = gt_speed_ms * math.sin(gt_course_rad)

    # Small random offset at release time (within 150m - 800m)
    gt_offset_m = rng.uniform(150.0, 800.0)
    gt_offset_angle = rng.uniform(0.0, 2 * math.pi)
    gt_release_lat = source_lat + (gt_offset_m * math.cos(gt_offset_angle)) / 111320.0
    gt_release_lon = source_lon + (gt_offset_m * math.sin(gt_offset_angle)) / (
        111320.0 * max(math.cos(lat_rad), 0.1)
    )

    # Generate AIS points every ~10 minutes across the window [release_time - 2h, obs_time + 1h]
    window_start = release_time - timedelta(hours=2)
    window_end = observation_time + timedelta(hours=1)
    ais_interval_min = 10.0
    total_minutes = int((window_end - window_start).total_seconds() / 60)

    gt_positions = []
    for m in range(0, total_minutes + 1, int(ais_interval_min)):
        p_time = window_start + timedelta(minutes=m)
        dt_from_release_s = (p_time - release_time).total_seconds()

        # Displacement from release point
        d_north_m = gt_v_north_ms * dt_from_release_s
        d_east_m = gt_v_east_ms * dt_from_release_s

        p_lat = gt_release_lat + d_north_m / 111320.0
        p_lon = gt_release_lon + d_east_m / (111320.0 * max(math.cos(lat_rad), 0.1))

        # Jitter heading and speed slightly (natural AIS measurement noise)
        p_speed = round(gt_speed_knots + rng.gauss(0.0, 0.2), 1)
        p_heading = round((gt_course_deg + rng.gauss(0.0, 1.5)) % 360.0, 1)

        gt_positions.append({
            "timestamp": p_time.isoformat(),
            "lat": round(float(p_lat), 6),
            "lon": round(float(p_lon), 6),
            "speed": max(0.0, p_speed),
            "heading": p_heading,
        })

    vessels.append({
        "id": gt_vessel_id,
        "mmsi": gt_mmsi,
        "vessel_name": gt_name,
        "vessel_type": "Tanker / Cargo",
        "positions": gt_positions,
        "is_ground_truth": True,
    })

    # Distractor Vessels
    distractor_types = ["Container Ship", "Bulk Carrier", "Chemical Tanker", "Ro-Ro", "Fishing Vessel"]
    distractor_prefixes = ["PACIFIC", "NORDIC", "ATLANTIC", "BALTIC", "MEDITERRANEAN", "EUROPA", "SEABOUND"]

    for d_idx in range(1, candidate_count):
        d_mmsi = str(rng.randint(300000000, 399999999) + d_idx)
        d_name = f"VESSEL-{rng.choice(distractor_prefixes)}-{rng.choice(['VOYAGER', 'TRADER', 'NAVIGATOR', 'PIONEER'])}-{rng.randint(100, 999)}"

        d_course_deg = rng.uniform(0.0, 360.0)
        d_speed_knots = rng.uniform(6.0, 18.0)
        d_speed_ms = d_speed_knots * 0.514444

        d_course_rad = math.radians(d_course_deg)
        d_v_north_ms = d_speed_ms * math.cos(d_course_rad)
        d_v_east_ms = d_speed_ms * math.sin(d_course_rad)

        # Distractor separation strategies:
        # d_idx == 1: Parallel/nearby track, offset by 6 - 15 km
        # d_idx == 2: Cross track crossing, temporal mismatch of 2-4 hours
        # d_idx >= 3: Distant traffic, offset by 20 - 50 km
        if d_idx == 1:
            offset_dist_km = rng.uniform(6.0, 14.0)
            offset_bearing = (gt_course_deg + 90.0) % 360.0
            time_shift_hours = rng.uniform(-1.0, 1.0)
        elif d_idx == 2:
            offset_dist_km = rng.uniform(12.0, 25.0)
            offset_bearing = rng.uniform(0.0, 360.0)
            time_shift_hours = rng.choice([-3.5, 3.5])
        else:
            offset_dist_km = rng.uniform(25.0, 60.0)
            offset_bearing = rng.uniform(0.0, 360.0)
            time_shift_hours = rng.uniform(-4.0, 4.0)

        offset_rad = math.radians(offset_bearing)
        ref_lat = source_lat + (offset_dist_km * 1000.0 * math.cos(offset_rad)) / 111320.0
        ref_lon = source_lon + (offset_dist_km * 1000.0 * math.sin(offset_rad)) / (
            111320.0 * max(math.cos(lat_rad), 0.1)
        )
        ref_time = release_time + timedelta(hours=time_shift_hours)

        d_positions = []
        for m in range(0, total_minutes + 1, int(ais_interval_min)):
            p_time = window_start + timedelta(minutes=m)
            dt_from_ref_s = (p_time - ref_time).total_seconds()

            d_north_m = d_v_north_ms * dt_from_ref_s
            d_east_m = d_v_east_ms * dt_from_ref_s

            p_lat = ref_lat + d_north_m / 111320.0
            p_lon = ref_lon + d_east_m / (111320.0 * max(math.cos(lat_rad), 0.1))

            p_speed = round(d_speed_knots + rng.gauss(0.0, 0.3), 1)
            p_heading = round((d_course_deg + rng.gauss(0.0, 1.5)) % 360.0, 1)

            d_positions.append({
                "timestamp": p_time.isoformat(),
                "lat": round(float(p_lat), 6),
                "lon": round(float(p_lon), 6),
                "speed": max(0.0, p_speed),
                "heading": p_heading,
            })

        vessels.append({
            "id": d_mmsi,
            "mmsi": d_mmsi,
            "vessel_name": d_name,
            "vessel_type": rng.choice(distractor_types),
            "positions": d_positions,
            "is_ground_truth": False,
        })

    # Shuffle vessels so ground truth is not always in position 0
    rng.shuffle(vessels)

    return SyntheticScenario(
        scenario_id=scenario_id,
        seed=seed,
        observation_time=observation_time,
        backtrack_hours=backtrack_hours,
        step_hours=step_hours,
        origin_lon=origin_lon,
        origin_lat=origin_lat,
        spill_area_m2=spill_area_m2,
        wind_speed_ms=wind_speed_ms,
        wind_direction_deg=wind_direction_deg,
        current_speed_ms=current_speed_ms,
        current_direction_deg=current_direction_deg,
        u10=u10,
        v10=v10,
        uo=uo,
        vo=vo,
        ground_truth_vessel_id=gt_vessel_id,
        vessels=vessels,
        estimated_source_lon=source_lon,
        estimated_source_lat=source_lat,
        wind_ds=wind_ds,
        curr_ds=curr_ds,
        is_synthetic=True,
    )
