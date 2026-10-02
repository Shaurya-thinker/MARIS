"""MARIS Real-Experiment — AIS Search Service.

Discovers AIS vessel tracks near a reconstructed source zone using the
configured AIS acquisition provider.

If the AIS adapter is unconfigured (MARIS_AIS_ADAPTER=unconfigured or unset)
the service returns an explicit ConfigurationUnavailable error so the wizard
UI can display:
    "AIS data source not configured. Set MARIS_AIS_ADAPTER and the required
    credentials to search for vessel tracks."

ZERO fabrication policy:
    This service NEVER generates synthetic vessel positions.
    If no positions are returned from the adapter, the result is an empty list.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.acquisition.base import AcquisitionConfigurationError, AcquisitionError
from app.acquisition.providers.ais import (
    AisAcquisitionProvider,
    AisHistoricalQuery,
    AisPositionRecord,
    UnconfiguredAisAdapter,
    normalize_ais_records,
)
from app.acquisition.schemas import AcquisitionRequest
from app.core.config import Settings, settings as default_settings
from app.models.common import AssetType, BBoxAreaOfInterest, BoundingBox, TimeWindow


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConfigurationUnavailable(Exception):
    """Raised when no AIS adapter is configured."""


class AisSearchError(Exception):
    """Raised when the AIS query fails."""


# ---------------------------------------------------------------------------
# Pure Spatial & Temporal Helpers
# ---------------------------------------------------------------------------

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def derive_drift_ais_corridor(
    *,
    backward_steps: list[Any],
    observation_time: datetime,
    backtrack_hours: float,
    buffer_km: float = 5.0,
) -> dict[str, Any]:
    """Derive dynamic spatial bounding box and temporal window from backward drift trajectory.

    The spatial envelope covers all trajectory steps and expands by each step's
    uncertainty_radius_m + buffer_km.
    The temporal window covers [source_time - 1 hour, observation_time].
    """
    if not backward_steps:
        raise ValueError("backward_steps cannot be empty when deriving drift AIS corridor")

    obs_utc = AisSearchService._utc(observation_time)
    source_time = obs_utc - timedelta(hours=backtrack_hours)
    window_start = source_time - timedelta(hours=1.0)
    window_end = obs_utc

    lats: list[float] = []
    lons: list[float] = []
    radii_km: list[float] = []

    for s in backward_steps:
        lat = getattr(s, "lat", None) if not isinstance(s, dict) else s.get("lat")
        lon = getattr(s, "lon", None) if not isinstance(s, dict) else s.get("lon")
        r_m = getattr(s, "uncertainty_radius_m", None) if not isinstance(s, dict) else s.get("uncertainty_radius_m")
        if r_m is None:
            r_m = 500.0

        if lat is not None and lon is not None and math.isfinite(lat) and math.isfinite(lon):
            lats.append(float(lat))
            lons.append(float(lon))
            radii_km.append(float(r_m) / 1000.0)

    if not lats:
        raise ValueError("backward_steps contained no valid coordinates")

    mid_lat = sum(lats) / len(lats)
    max_radius_km = max(radii_km) if radii_km else 1.0
    expand_km = max_radius_km + buffer_km

    # Conversion of km to degrees:
    # 1 deg latitude ≈ 111.139 km
    # 1 deg longitude ≈ 111.139 km * cos(mid_lat)
    deg_lat = expand_km / 111.139
    cos_lat = max(math.cos(math.radians(mid_lat)), 0.1)
    deg_lon = expand_km / (111.139 * cos_lat)

    south = max(-90.0, min(lats) - deg_lat)
    north = min(90.0, max(lats) + deg_lat)
    west = max(-180.0, min(lons) - deg_lon)
    east = min(180.0, max(lons) + deg_lon)

    return {
        "west": round(west, 5),
        "south": round(south, 5),
        "east": round(east, 5),
        "north": round(north, 5),
        "source_time": source_time,
        "observation_time": obs_utc,
        "window_start": window_start,
        "window_end": window_end,
        "max_uncertainty_radius_km": round(max_radius_km, 3),
        "step_count": len(backward_steps),
    }


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass
class VesselTrackSummary:
    """AIS track summary for one vessel, returned to the wizard UI."""

    mmsi: str | None
    vessel_name: str | None
    imo: str | None
    position_count: int
    first_timestamp: datetime
    last_timestamp: datetime
    source_adapter: str
    positions: list[dict[str, Any]] = field(default_factory=list)
    min_trajectory_distance_km: float | None = None
    trajectory_time_delta_hours: float | None = None
    source_zone_intersection: bool = False
    min_source_distance_km: float | None = None
    source_type: str | None = None
    provider_name: str | None = None
    vessel_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mmsi": self.mmsi,
            "vessel_name": self.vessel_name,
            "imo": self.imo,
            "position_count": self.position_count,
            "first_timestamp": self.first_timestamp.isoformat() if isinstance(self.first_timestamp, datetime) else str(self.first_timestamp),
            "last_timestamp": self.last_timestamp.isoformat() if isinstance(self.last_timestamp, datetime) else str(self.last_timestamp),
            "source_adapter": self.source_adapter,
            "positions": self.positions,
            "min_trajectory_distance_km": self.min_trajectory_distance_km,
            "trajectory_time_delta_hours": self.trajectory_time_delta_hours,
            "source_zone_intersection": self.source_zone_intersection,
            "min_source_distance_km": self.min_source_distance_km,
            "source_type": self.source_type,
            "provider_name": self.provider_name,
            "vessel_type": self.vessel_type,
        }


@dataclass
class AisSearchResult:
    """Aggregated AIS search result."""

    vessels: list[VesselTrackSummary] = field(default_factory=list)
    total_positions: int = 0
    search_bbox: dict[str, float] = field(default_factory=dict)
    search_window_start: str = ""
    search_window_end: str = ""
    adapter_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "vessels": [v.as_dict() for v in self.vessels],
            "total_positions": self.total_positions,
            "search_bbox": self.search_bbox,
            "search_window_start": self.search_window_start,
            "search_window_end": self.search_window_end,
            "adapter_id": self.adapter_id,
        }


def search_along_drift_corridor(
    *,
    backward_steps: list[Any],
    observation_time: datetime,
    backtrack_hours: float,
    source_candidate_zone: dict[str, Any] | None = None,
    buffer_km: float = 5.0,
    db_path: Path | str | None = None,
) -> list[VesselTrackSummary]:
    """Execute dynamic AIS corridor query against ais_vessels.db and compute track correlation.

    1. Derive the dynamic corridor bounding box and temporal window from backward_steps.
    2. Query ais_vessels.db using SqliteAisAdapter (query_vessels_in_spatiotemporal_box).
    3. For each returned vessel track, calculate:
       - min_trajectory_distance_km (CPA to any backward drift step)
       - trajectory_time_delta_hours (temporal delta between position and closest drift step)
       - source_zone_intersection (whether vessel enters source zone within +/- 1.5h of source time)
       - source_type and provider_name (authentic database provenance)
    4. ZERO-FABRICATION RULE: Never interpolate, dead-reckon, or fabricate positions.
    """
    if not backward_steps:
        return []

    corridor = derive_drift_ais_corridor(
        backward_steps=backward_steps,
        observation_time=observation_time,
        backtrack_hours=backtrack_hours,
        buffer_km=buffer_km,
    )

    # Reconstruct source center and radius from earliest backward step
    earliest_step = backward_steps[-1]
    source_lon = getattr(earliest_step, "lon", None) if not isinstance(earliest_step, dict) else earliest_step.get("lon")
    source_lat = getattr(earliest_step, "lat", None) if not isinstance(earliest_step, dict) else earliest_step.get("lat")
    source_radius_m = getattr(earliest_step, "uncertainty_radius_m", None) if not isinstance(earliest_step, dict) else earliest_step.get("uncertainty_radius_m")
    if source_radius_m is None:
        source_radius_m = 5000.0
    source_radius_km = float(source_radius_m) / 1000.0

    # Query authentic SQLite AIS database
    from app.services.real_experiment.ais_database import query_vessels_in_spatiotemporal_box
    resolved_db_path = Path(db_path) if db_path is not None else None

    vessel_records = query_vessels_in_spatiotemporal_box(
        lat_min=corridor["south"],
        lat_max=corridor["north"],
        lon_min=corridor["west"],
        lon_max=corridor["east"],
        t_start=corridor["window_start"],
        t_end=corridor["window_end"],
        db_path=resolved_db_path,
    )

    t_source = corridor["source_time"]

    summaries: list[VesselTrackSummary] = []
    for v in vessel_records:
        mmsi = v.get("mmsi")
        vessel_name = v.get("vessel_name")
        vessel_type = v.get("vessel_type")
        imo = v.get("imo")
        source_type = v.get("source_type") or "sqlite_ais"
        provider_name = v.get("provider_name") or "ais_vessels.db"
        positions = v.get("positions", [])

        # Parse valid positions
        parsed_pos: list[dict[str, Any]] = []
        for p in positions:
            ts = p.get("timestamp")
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    ts = None
            if ts and p.get("lat") is not None and p.get("lon") is not None:
                parsed_pos.append({
                    "timestamp": ts.isoformat().replace("+00:00", "Z"),
                    "t": AisSearchService._utc(ts),
                    "lat": float(p["lat"]),
                    "lon": float(p["lon"]),
                    "speed": p.get("speed"),
                    "heading": p.get("heading"),
                })
        parsed_pos.sort(key=lambda x: x["t"])

        min_traj_dist_km: float | None = None
        traj_time_delta_h: float | None = None
        min_source_dist_km: float | None = None
        source_zone_intersect = False

        if parsed_pos:
            # 1. Minimum distance to reconstructed source point
            if source_lat is not None and source_lon is not None:
                for p in parsed_pos:
                    d_src = _haversine_km(p["lat"], p["lon"], source_lat, source_lon)
                    if min_source_dist_km is None or d_src < min_source_dist_km:
                        min_source_dist_km = d_src

            # 2. Track proximity (CPA) against all backward_steps
            for p in parsed_pos:
                pos_t = p["t"]
                pos_lat = p["lat"]
                pos_lon = p["lon"]
                for step in backward_steps:
                    s_lon = getattr(step, "lon", None) if not isinstance(step, dict) else step.get("lon")
                    s_lat = getattr(step, "lat", None) if not isinstance(step, dict) else step.get("lat")
                    s_t = getattr(step, "timestamp", None) if not isinstance(step, dict) else step.get("timestamp")
                    if s_lon is None or s_lat is None:
                        continue
                    d = _haversine_km(pos_lat, pos_lon, s_lat, s_lon)
                    if min_traj_dist_km is None or d < min_traj_dist_km:
                        min_traj_dist_km = d
                        if s_t is not None:
                            if isinstance(s_t, str):
                                try:
                                    s_t = datetime.fromisoformat(s_t.replace("Z", "+00:00"))
                                except Exception:
                                    s_t = None
                            if s_t is not None:
                                traj_time_delta_h = abs((pos_t - AisSearchService._utc(s_t)).total_seconds()) / 3600.0

            # 3. Source zone intersection: position within source zone around source time (+/- 1.5h)
            if source_lat is not None and source_lon is not None:
                for p in parsed_pos:
                    dt_source_h = abs((p["t"] - t_source).total_seconds()) / 3600.0
                    if dt_source_h <= 1.5:
                        d_src = _haversine_km(p["lat"], p["lon"], source_lat, source_lon)
                        if d_src <= source_radius_km:
                            source_zone_intersect = True
                            break

        serialized_pos = [
            {
                "timestamp": p["timestamp"],
                "lat": p["lat"],
                "lon": p["lon"],
                "speed": p["speed"],
                "heading": p["heading"],
            }
            for p in parsed_pos
        ]

        t_first = parsed_pos[0]["t"] if parsed_pos else corridor["window_start"]
        t_last = parsed_pos[-1]["t"] if parsed_pos else corridor["window_end"]

        summaries.append(
            VesselTrackSummary(
                mmsi=mmsi,
                vessel_name=vessel_name,
                imo=imo,
                position_count=len(serialized_pos),
                first_timestamp=t_first,
                last_timestamp=t_last,
                source_adapter="SqliteAisAdapter",
                positions=serialized_pos,
                min_trajectory_distance_km=round(min_traj_dist_km, 3) if min_traj_dist_km is not None else None,
                trajectory_time_delta_hours=round(traj_time_delta_h, 3) if traj_time_delta_h is not None else None,
                source_zone_intersection=source_zone_intersect,
                min_source_distance_km=round(min_source_dist_km, 3) if min_source_dist_km is not None else None,
                source_type=source_type,
                provider_name=provider_name,
                vessel_type=vessel_type,
            )
        )

    # Sort candidates by:
    # 1. Source zone intersection (True first)
    # 2. Trajectory proximity (closest first)
    summaries.sort(
        key=lambda s: (
            0 if s.source_zone_intersection else 1,
            s.min_trajectory_distance_km if s.min_trajectory_distance_km is not None else 9999.0,
        )
    )

    return summaries


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class AisSearchService:
    """Search for AIS vessel tracks using the configured adapter.

    Usage::

        svc = AisSearchService()
        result = svc.search_near_source_zone(
            west=55.0, south=20.0, east=60.0, north=25.0,
            start=datetime(2024, 6, 1, 2, 0, tzinfo=timezone.utc),
            end=datetime(2024, 6, 1, 14, 0, tzinfo=timezone.utc),
        )
    """

    def __init__(
        self,
        cfg: Settings | None = None,
        ais_provider: AisAcquisitionProvider | None = None,
    ) -> None:
        self._cfg = cfg or default_settings
        self._ais = ais_provider or AisAcquisitionProvider(settings=self._cfg)

    def is_configured(self) -> bool:
        """Return True if a real AIS adapter is configured."""
        adapter_id = getattr(self._cfg, "ais_adapter_id", "unconfigured")
        return adapter_id not in ("unconfigured", "", None)

    def search_near_source_zone(
        self,
        *,
        west: float,
        south: float,
        east: float,
        north: float,
        start: datetime,
        end: datetime,
        investigation_id: str = "real-experiment",
    ) -> AisSearchResult:
        """Find vessels whose AIS positions fall within the given bbox and time window.

        Args:
            west/south/east/north: spatial search box (WGS84).
            start/end: temporal search window (UTC).
            investigation_id: logical grouping for asset naming.

        Returns:
            AisSearchResult with per-vessel summaries.

        Raises:
            ConfigurationUnavailable: AIS adapter not configured.
            AisSearchError: adapter call failed.
        """
        if not self.is_configured():
            raise ConfigurationUnavailable(
                "AIS data source not configured. "
                "Set MARIS_AIS_ADAPTER and the required credentials to search for vessel tracks. "
                "Contact your MARIS administrator to configure an AIS provider."
            )

        start_utc = self._utc(start)
        end_utc = self._utc(end)

        bbox = BBoxAreaOfInterest(
            bbox=BoundingBox(west=west, south=south, east=east, north=north)
        )
        req = AcquisitionRequest(
            investigation_id=investigation_id,
            provider_id="ais",
            asset_type=AssetType.VESSEL_TRACK,
            area_of_interest=bbox,
            time_window=TimeWindow(start=start_utc, end=end_utc),
        )

        try:
            result = self._ais.acquire(req)
        except AcquisitionConfigurationError as exc:
            raise ConfigurationUnavailable(str(exc)) from exc
        except AcquisitionError as exc:
            raise AisSearchError(f"AIS search failed: {exc}") from exc

        # Parse the written artifact JSON back into AisPositionRecord objects
        import json
        all_records: list[AisPositionRecord] = []
        for artifact in result.artifacts:
            try:
                raw = json.loads(Path(artifact.location).read_text(encoding="utf-8"))
                records_data = raw.get("records", raw) if isinstance(raw, dict) else raw
                if isinstance(records_data, list):
                    query = AisHistoricalQuery(
                        investigation_id=investigation_id,
                        area_of_interest=bbox,
                        time_window=TimeWindow(start=start_utc, end=end_utc),
                    )
                    all_records.extend(normalize_ais_records(records_data, query))
            except Exception:
                pass  # Skip artifacts that cannot be parsed; do not fabricate data

        return self._group_by_vessel(
            all_records,
            west=west, south=south, east=east, north=north,
            start=start_utc, end=end_utc,
            adapter_id=getattr(self._cfg, "ais_adapter_id", "unknown"),
        )

    def get_vessel_positions(
        self,
        *,
        mmsis: list[str],
        west: float | None = None,
        south: float | None = None,
        east: float | None = None,
        north: float | None = None,
        start: datetime,
        end: datetime,
    ) -> dict[str, list[dict[str, Any]]]:
        """Retrieve authentic historical AIS position records for specific MMSIs.

        Strictly bounded by time window and optional geographic bbox.
        """
        if not self.is_configured():
            raise ConfigurationUnavailable("AIS data source not configured.")
        from app.services.real_experiment.ais_database import query_positions_for_mmsis
        start_utc = self._utc(start)
        end_utc = self._utc(end)
        return query_positions_for_mmsis(
            mmsis=mmsis,
            t_start=start_utc,
            t_end=end_utc,
            lat_min=south,
            lat_max=north,
            lon_min=west,
            lon_max=east,
        )

    def search_along_drift_corridor(
        self,
        *,
        backward_steps: list[Any],
        observation_time: datetime,
        backtrack_hours: float,
        buffer_km: float = 5.0,
        investigation_id: str = "real-experiment",
        db_path: Path | None = None,
    ) -> AisSearchResult:
        """Search for AIS vessels within the dynamic spatiotemporal corridor of a backward drift trajectory."""
        if not self.is_configured():
            raise ConfigurationUnavailable(
                "AIS data source not configured. "
                "Set MARIS_AIS_ADAPTER and the required credentials to search for vessel tracks. "
                "Contact your MARIS administrator to configure an AIS provider."
            )

        corridor = derive_drift_ais_corridor(
            backward_steps=backward_steps,
            observation_time=observation_time,
            backtrack_hours=backtrack_hours,
            buffer_km=buffer_km,
        )

        # Query using the dynamic corridor bounds
        result = self.search_near_source_zone(
            west=corridor["west"],
            south=corridor["south"],
            east=corridor["east"],
            north=corridor["north"],
            start=corridor["window_start"],
            end=corridor["window_end"],
            investigation_id=investigation_id,
        )

        # Normalize steps for trajectory CPA and source intersection
        norm_steps = []
        for s in backward_steps:
            lat = getattr(s, "lat", None) if not isinstance(s, dict) else s.get("lat")
            lon = getattr(s, "lon", None) if not isinstance(s, dict) else s.get("lon")
            ts = getattr(s, "timestamp", None) if not isinstance(s, dict) else s.get("timestamp")
            r_m = getattr(s, "uncertainty_radius_m", None) if not isinstance(s, dict) else s.get("uncertainty_radius_m")
            if ts is not None and isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            norm_steps.append({
                "lat": float(lat) if lat is not None else 0.0,
                "lon": float(lon) if lon is not None else 0.0,
                "timestamp": ts,
                "radius_km": (float(r_m) if r_m is not None else 500.0) / 1000.0,
            })

        final_step = norm_steps[-1] if norm_steps else None
        source_lat = final_step["lat"] if final_step else 0.0
        source_lon = final_step["lon"] if final_step else 0.0
        source_radius_km = final_step["radius_km"] if final_step else 0.5
        source_time = corridor["source_time"]

        # Fetch provenance metadata from database
        mmsi_list = [v.mmsi for v in result.vessels if v.mmsi]
        prov_map = self._fetch_vessel_provenance(mmsi_list, db_path=db_path)

        for v in result.vessels:
            parsed_pos = []
            for p in v.positions:
                try:
                    ts = p.get("timestamp")
                    if isinstance(ts, str):
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if ts and ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    parsed_pos.append({
                        "lat": float(p["lat"]),
                        "lon": float(p["lon"]),
                        "t": ts,
                    })
                except Exception:
                    pass

            min_traj_dist: float | None = None
            traj_dt_hours: float | None = None
            min_source_dist: float | None = None
            source_intersection = False

            if parsed_pos and norm_steps:
                # 1. Trajectory CPA
                for p in parsed_pos:
                    for s in norm_steps:
                        d = _haversine_km(p["lat"], p["lon"], s["lat"], s["lon"])
                        dt_h = abs((p["t"] - s["timestamp"]).total_seconds()) / 3600.0 if (p["t"] and s["timestamp"]) else 0.0
                        if min_traj_dist is None or d < min_traj_dist:
                            min_traj_dist = d
                            traj_dt_hours = dt_h

                # 2. Source-zone intersection & distance
                for p in parsed_pos:
                    d_src = _haversine_km(p["lat"], p["lon"], source_lat, source_lon)
                    if min_source_dist is None or d_src < min_source_dist:
                        min_source_dist = d_src
                    if p["t"]:
                        dt_src_h = abs((p["t"] - source_time).total_seconds()) / 3600.0
                        if dt_src_h <= 1.5 and d_src <= source_radius_km:
                            source_intersection = True

            v.min_trajectory_distance_km = round(min_traj_dist, 3) if min_traj_dist is not None else None
            v.trajectory_time_delta_hours = round(traj_dt_hours, 3) if traj_dt_hours is not None else None
            v.min_source_distance_km = round(min_source_dist, 3) if min_source_dist is not None else None
            v.source_zone_intersection = source_intersection

            v_key = v.mmsi or v.vessel_name
            if v_key and v_key in prov_map:
                v.source_type = prov_map[v_key].get("source_type")
                v.provider_name = prov_map[v_key].get("provider_name")

        # Sort: source zone intersection first, then minimum trajectory distance
        result.vessels.sort(
            key=lambda v: (
                0 if v.source_zone_intersection else 1,
                v.min_trajectory_distance_km if v.min_trajectory_distance_km is not None else 99999.0,
                -(v.position_count or 0),
            )
        )

        return result

    @staticmethod
    def _fetch_vessel_provenance(
        identifiers: list[str],
        db_path: Path | None = None,
    ) -> dict[str, dict[str, Any]]:
        if not identifiers:
            return {}
        from app.services.real_experiment.ais_database import get_connection
        conn = get_connection(db_path)
        prov: dict[str, dict[str, Any]] = {}
        try:
            cur = conn.cursor()
            placeholders = ",".join("?" for _ in identifiers)
            cur.execute(
                f"""\
                SELECT v.mmsi, v.vessel_id, v.vessel_name, v.source_type, ds.provider_name
                FROM vessels v
                JOIN data_sources ds ON v.source_id = ds.source_id
                WHERE v.mmsi IN ({placeholders}) OR v.vessel_id IN ({placeholders});
                """,
                (*identifiers, *identifiers),
            )
            for r in cur.fetchall():
                meta = {
                    "source_type": r["source_type"],
                    "provider_name": r["provider_name"],
                }
                if r["mmsi"]:
                    prov[r["mmsi"]] = meta
                if r["vessel_id"]:
                    prov[r["vessel_id"]] = meta
                if r["vessel_name"]:
                    prov[r["vessel_name"]] = meta
        except Exception:
            pass
        finally:
            conn.close()
        return prov

    @staticmethod
    def _group_by_vessel(
        records: list[AisPositionRecord],
        *,
        west: float, south: float, east: float, north: float,
        start: datetime, end: datetime,
        adapter_id: str,
    ) -> AisSearchResult:
        """Group position records by vessel identifier and build track summaries."""
        from collections import defaultdict

        groups: dict[str, list[AisPositionRecord]] = defaultdict(list)
        for rec in records:
            key = rec.mmsi or rec.vessel_name or "unknown"
            groups[key].append(rec)

        summaries: list[VesselTrackSummary] = []
        for key, positions in groups.items():
            sorted_pos = sorted(positions, key=lambda p: p.timestamp)
            sample = sorted_pos[0]
            v_positions = [
                {
                    "timestamp": p.timestamp.isoformat().replace("+00:00", "Z") if isinstance(p.timestamp, datetime) else str(p.timestamp),
                    "lat": p.lat,
                    "lon": p.lon,
                    "speed": p.speed_over_ground,
                    "heading": p.heading,
                    "cog": p.course_over_ground,
                    "nav_status": p.navigation_status,
                }
                for p in sorted_pos
            ]
            summaries.append(
                VesselTrackSummary(
                    mmsi=sample.mmsi,
                    vessel_name=sample.vessel_name,
                    imo=sample.imo,
                    position_count=len(sorted_pos),
                    first_timestamp=sorted_pos[0].timestamp,
                    last_timestamp=sorted_pos[-1].timestamp,
                    source_adapter=adapter_id,
                    positions=v_positions,
                )
            )

        # Sort: most positions first (more data = more informative)
        summaries.sort(key=lambda s: s.position_count, reverse=True)

        return AisSearchResult(
            vessels=summaries,
            total_positions=len(records),
            search_bbox={"west": west, "south": south, "east": east, "north": north},
            search_window_start=start.isoformat(),
            search_window_end=end.isoformat(),
            adapter_id=adapter_id,
        )

    @staticmethod
    def _utc(dt: datetime | str) -> datetime:
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)


def Path_read(path: str) -> str:
    """Read text content from a file path (helper to avoid importing Path in a loop)."""
    from pathlib import Path
    return Path(path).read_text(encoding="utf-8")
