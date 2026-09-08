"""MARIS Stage E1 — Candidate Vessel Generation Service.

Generates deterministic candidate vessels from genuine historical AIS observations
that are spatially and temporally relevant to the D3 source candidate zone.

ZERO-FABRICATION INVARIANT:
Never interpolate, dead-reckon, reconstruct, infer, or fabricate an AIS position.
A vessel may become an E1 candidate ONLY because an actual observed AIS position falls:
- inside the D3 source candidate zone, OR
- within the configured spatial buffer around it.
Do NOT infer zone crossings from gaps between AIS observations.

Asset Registration:
The derived candidate-analysis artifact is registered as AssetType.DOCUMENT with
metadata {"asset_type": "candidate_vessels"}. Original AIS data remains AssetType.VESSEL_TRACK.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.acquisition.base import AcquisitionConfigurationError
from app.acquisition.providers.ais import (
    AisAcquisitionProvider,
    AisPositionRecord,
)
from app.acquisition.registry import AssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact, AcquisitionRequest
from app.core.config import settings
from app.models.asset import Asset
from app.models.common import (
    AssetType,
    BBoxAreaOfInterest,
    BoundingBox,
    PolygonAreaOfInterest,
    Provenance,
    TimeWindow,
)
from app.models.source_estimation import SourceEstimateResult
from app.models.vessel import (
    CandidateGenerationStatus,
    CandidateVessel,
    CandidateVesselGenerationResult,
    VesselPosition,
)
from app.services.drift_modelling import _haversine_m, _sanitize, _utc
from app.validation.ais import AisValidator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TEMPORAL_WINDOW_HOURS = 2.0
DEFAULT_SPATIAL_BUFFER_KM = 0.0

_KM_PER_DEG_LAT = 111.32


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CandidateVesselError(Exception):
    """Raised when candidate vessel generation fails."""


class AisValidationFailureError(CandidateVesselError):
    """Raised when explicitly supplied AIS data fails scientific validation."""


# ---------------------------------------------------------------------------
# Pure Geometric Utilities
# ---------------------------------------------------------------------------

def point_in_polygon(lon: float, lat: float, ring: list[list[float]]) -> bool:
    """Ray-casting algorithm (even-odd rule) for 2D point-in-polygon containment.

    ring is expected to be a closed linear ring of [lon, lat] coordinates (RFC 7946).
    """
    if len(ring) < 4:
        return False

    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]

        # Check ray intersection with segment (xj, yj)-(xi, yi)
        if ((yi > lat) != (yj > lat)):
            denom = yj - yi
            if abs(denom) > 1e-15:
                x_intersection = (xj - xi) * (lat - yi) / denom + xi
                if lon < x_intersection:
                    inside = not inside
        j = i

    return inside


def derive_spatial_bounding_box(
    polygon_ring: list[list[float]],
    spatial_buffer_km: float = 0.0,
) -> dict[str, float]:
    """Derive an axis-aligned bounding box enclosing the source polygon plus buffer."""
    if not polygon_ring:
        raise CandidateVesselError("Polygon ring coordinates cannot be empty")

    lons = [pt[0] for pt in polygon_ring]
    lats = [pt[1] for pt in polygon_ring]

    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)

    center_lat = (min_lat + max_lat) / 2.0
    cos_lat = math.cos(math.radians(center_lat))
    if abs(cos_lat) < 1e-6:
        cos_lat = 1e-6

    d_lat = spatial_buffer_km / _KM_PER_DEG_LAT
    d_lon = spatial_buffer_km / (_KM_PER_DEG_LAT * cos_lat)

    west = max(-180.0, min_lon - d_lon)
    east = min(180.0, max_lon + d_lon)
    south = max(-90.0, min_lat - d_lat)
    north = min(90.0, max_lat + d_lat)

    return {
        "west": round(west, 7),
        "south": round(south, 7),
        "east": round(east, 7),
        "north": round(north, 7),
    }


def derive_temporal_window(
    source_time: datetime,
    observation_time: datetime,
    temporal_window_hours: float = DEFAULT_TEMPORAL_WINDOW_HOURS,
    earliest_available: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Derive candidate search temporal bounds clamped to observation_time."""
    if temporal_window_hours <= 0.0:
        raise CandidateVesselError(
            f"temporal_window_hours must be > 0, got {temporal_window_hours}"
        )

    s_time = _utc(source_time)
    obs_time = _utc(observation_time)

    delta = timedelta(hours=temporal_window_hours)
    window_start = s_time - delta
    if earliest_available is not None:
        window_start = max(window_start, _utc(earliest_available))

    # Never allow the temporal window to extend beyond the spill observation time
    window_end = min(s_time + delta, obs_time)

    if window_start >= window_end:
        raise CandidateVesselError(
            f"Derived temporal window is degenerate: start ({window_start.isoformat()}) "
            f"is not before end ({window_end.isoformat()})"
        )

    return window_start, window_end


# ---------------------------------------------------------------------------
# AIS Record Parsing & Loading
# ---------------------------------------------------------------------------

def parse_ais_records_from_artifact(artifact_path: Path) -> list[dict[str, Any]]:
    """Parse raw records collection from an AIS JSON artifact."""
    if not artifact_path.exists():
        raise CandidateVesselError(f"AIS artifact does not exist: {artifact_path}")
    try:
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CandidateVesselError(f"Malformed AIS JSON artifact: {exc}") from exc

    if not isinstance(data, dict):
        raise CandidateVesselError("AIS JSON artifact must be an object")

    records = data.get("records") if "records" in data else data.get("positions")
    if records is None:
        raise CandidateVesselError("AIS artifact missing records or positions array")
    if not isinstance(records, list):
        raise CandidateVesselError("AIS artifact records must be a list")

    return records


def load_source_estimate_from_asset(asset: Asset) -> SourceEstimateResult:
    """Reconstruct a SourceEstimateResult from a registered DRIFT_PRODUCT asset."""
    artifact_path = Path(asset.location)
    if not artifact_path.exists():
        raise CandidateVesselError(f"Source estimate artifact does not exist: {artifact_path}")

    try:
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CandidateVesselError(f"Malformed source estimate GeoJSON: {exc}") from exc

    features = data.get("features", [])
    if len(features) < 2:
        raise CandidateVesselError("Source estimate GeoJSON must contain at least 2 features")

    line_feat = next((f for f in features if f.get("properties", {}).get("feature_kind") == "backward_drift_centerline"), features[0])
    poly_feat = next((f for f in features if f.get("properties", {}).get("feature_kind") == "source_candidate_zone"), features[1])

    line_props = line_feat.get("properties", {})
    poly_props = poly_feat.get("properties", {})

    obs_time_str = line_props.get("observation_time") or asset.provenance.extra.get("observation_time")
    source_time_str = poly_props.get("source_time") or line_props.get("source_time")
    if not obs_time_str or not source_time_str:
        raise CandidateVesselError("Source estimate GeoJSON missing observation_time or source_time")

    obs_time = _utc(datetime.fromisoformat(obs_time_str.replace("Z", "+00:00")))
    source_time = _utc(datetime.fromisoformat(source_time_str.replace("Z", "+00:00")))

    center_lon = poly_props.get("center_lon")
    center_lat = poly_props.get("center_lat")
    radius_km = poly_props.get("uncertainty_radius_km")

    if center_lon is None or center_lat is None or radius_km is None:
        raise CandidateVesselError("Source candidate zone feature missing center or radius properties")

    coords = line_feat.get("geometry", {}).get("coordinates", [[center_lon, center_lat]])
    origin_lon = coords[0][0] if coords else center_lon
    origin_lat = coords[0][1] if coords else center_lat

    spill_id = poly_props.get("spill_detection_id") or asset.provenance.product_id or "unknown-spill"
    lookback_h = float(line_props.get("lookback_hours") or asset.provenance.extra.get("lookback_hours") or 12.0)
    step_h = float(asset.provenance.extra.get("step_hours") or 1.0)
    leeway = float(asset.provenance.extra.get("leeway_fraction") or 0.035)

    return SourceEstimateResult(
        id=f"source-{_sanitize(spill_id, 'spill')}-{_sanitize(asset.investigation_id, 'inv')}",
        investigation_id=asset.investigation_id,
        spill_detection_id=spill_id,
        wind_asset_id=asset.provenance.extra.get("wind_asset_id", "unknown-wind"),
        current_asset_id=asset.provenance.extra.get("current_asset_id", "unknown-current"),
        asset_id=asset.id,
        model_version=asset.provenance.extra.get("model_version", "leeway_euler_backward_v1"),
        observation_time=obs_time,
        origin_lon=float(origin_lon),
        origin_lat=float(origin_lat),
        source_time=source_time,
        source_point_lon=float(center_lon),
        source_point_lat=float(center_lat),
        lookback_hours=lookback_h,
        step_hours=step_h,
        leeway_fraction=leeway,
        source_uncertainty_radius_km=float(radius_km),
        steps=[],
        source_zone_geometry=poly_feat.get("geometry", {}),
        metadata=dict(asset.metadata),
    )



# ---------------------------------------------------------------------------
# Core Candidate Generation Service
# ---------------------------------------------------------------------------

def generate_candidate_vessels_for_spill(
    investigation_id: str,
    spill_id: str,
    source_estimate: SourceEstimateResult,
    ais_asset_id: str | None = None,
    temporal_window_hours: float = DEFAULT_TEMPORAL_WINDOW_HOURS,
    spatial_buffer_km: float = DEFAULT_SPATIAL_BUFFER_KM,
    output_dir: str | Path | None = None,
    registry: AssetRegistry | None = None,
    ais_provider: AisAcquisitionProvider | None = None,
) -> tuple[CandidateVesselGenerationResult, Asset | None]:
    """Generate candidate vessels from historical AIS observations for a D3 source zone.

    Strict Zero-Fabrication Invariant:
    - Never interpolate or invent positions.
    - An observation qualifies ONLY if genuine AIS observation coordinates fall
      inside the D3 candidate zone polygon or within spatial_buffer_km.

    Returns:
        (CandidateVesselGenerationResult, Asset | None):
            Candidate result and registered DOCUMENT asset (or None if AIS unavailable).
    """
    target_registry = registry or default_asset_registry

    # 1. Input parameter validation
    if temporal_window_hours <= 0.0:
        raise CandidateVesselError(f"temporal_window_hours must be > 0, got {temporal_window_hours}")
    if spatial_buffer_km < 0.0:
        raise CandidateVesselError(f"spatial_buffer_km must be >= 0, got {spatial_buffer_km}")

    # Validate source estimate fields
    source_center_lon = source_estimate.source_point_lon
    source_center_lat = source_estimate.source_point_lat
    source_time = _utc(source_estimate.source_time)
    observation_time = _utc(source_estimate.observation_time)
    uncertainty_radius_km = source_estimate.source_uncertainty_radius_km

    if not (math.isfinite(source_center_lon) and math.isfinite(source_center_lat)):
        raise CandidateVesselError(
            f"Non-finite source center coordinates: ({source_center_lon}, {source_center_lat})"
        )
    if not (-180.0 <= source_center_lon <= 180.0 and -90.0 <= source_center_lat <= 90.0):
        raise CandidateVesselError(
            f"Source center coordinates out of WGS84 range: ({source_center_lon}, {source_center_lat})"
        )
    if uncertainty_radius_km < 0.0:
        raise CandidateVesselError(
            f"Negative source uncertainty radius: {uncertainty_radius_km}"
        )

    # Validate source candidate polygon geometry
    geom = source_estimate.source_zone_geometry
    if not isinstance(geom, dict) or geom.get("type") != "Polygon":
        raise CandidateVesselError("source_zone_geometry must be a GeoJSON Polygon")
    coords = geom.get("coordinates")
    if not coords or not isinstance(coords, list) or not coords[0] or len(coords[0]) < 4:
        raise CandidateVesselError("source_zone_geometry must have a closed linear ring with >= 4 vertices")
    polygon_ring = coords[0]

    # 2. Derive spatial bounding box and temporal search window
    spatial_bbox = derive_spatial_bounding_box(polygon_ring, spatial_buffer_km)
    temporal_start, temporal_end = derive_temporal_window(
        source_time=source_time,
        observation_time=observation_time,
        temporal_window_hours=temporal_window_hours,
    )

    # 3. Resolve AIS data source
    ais_asset: Asset | None = None
    resolved_ais_id = ais_asset_id

    if ais_asset_id:
        try:
            ais_asset = target_registry.get(ais_asset_id)
        except KeyError:
            raise CandidateVesselError(f"AIS asset '{ais_asset_id}' not found in registry")

        if ais_asset.type != AssetType.VESSEL_TRACK:
            raise CandidateVesselError(
                f"Asset '{ais_asset_id}' has type '{ais_asset.type}', expected '{AssetType.VESSEL_TRACK}'"
            )

        # Scientific validation of the explicit AIS artifact
        validator = AisValidator()
        acq_artifact = AcquiredArtifact(
            asset_type=ais_asset.type,
            location=ais_asset.location,
            source=ais_asset.source,
            acquisition_time=ais_asset.acquisition_time,
            provenance=ais_asset.provenance,
            metadata=ais_asset.metadata,
        )
        val_result = validator.validate(acq_artifact)
        if not val_result.passed:
            errors = [i for i in val_result.issues if i.severity.value == "error"]
            if all(i.code == "AIS_POSITIONS_EMPTY" for i in errors):
                # Empty AIS artifact -> valid collection but zero positions -> NO_CANDIDATES_FOUND
                res_id = f"cand-gen-{_sanitize(spill_id, 'spill')}-{_sanitize(investigation_id, 'inv')}"
                return (
                    CandidateVesselGenerationResult(
                        id=res_id,
                        investigation_id=investigation_id,
                        source_estimate_id=source_estimate.id,
                        spill_detection_id=spill_id,
                        ais_asset_id=resolved_ais_id,
                        derived_asset_id=None,
                        status=CandidateGenerationStatus.NO_CANDIDATES_FOUND,
                        source_time=source_time,
                        source_uncertainty_radius_km=uncertainty_radius_km,
                        temporal_window_start=temporal_start,
                        temporal_window_end=temporal_end,
                        spatial_query_bbox=spatial_bbox,
                        candidate_count=0,
                        total_vessels_checked=0,
                        candidates=[],
                        metadata={
                            "message": "AIS dataset contains zero position records.",
                            "zero_fabrication_guarantee": True,
                        },
                    ),
                    None,
                )
            issues_summary = "; ".join(f"{i.code}: {i.message}" for i in errors)
            raise AisValidationFailureError(
                f"Explicit AIS asset '{ais_asset_id}' failed scientific validation: {issues_summary}"
            )

    else:
        # Check if an existing VESSEL_TRACK asset is registered for this investigation
        for asset in target_registry.list_for_investigation(investigation_id):
            if asset.type == AssetType.VESSEL_TRACK:
                ais_asset = asset
                resolved_ais_id = asset.id
                break

    # If no AIS asset exists, attempt acquisition via the AisAcquisitionProvider boundary
    raw_records: list[dict[str, Any]] = []
    if ais_asset is not None:
        raw_records = parse_ais_records_from_artifact(Path(ais_asset.location))
    else:
        # Request acquisition through provider
        provider = ais_provider or AisAcquisitionProvider()
        bbox_obj = BoundingBox(
            west=spatial_bbox["west"],
            south=spatial_bbox["south"],
            east=spatial_bbox["east"],
            north=spatial_bbox["north"],
        )
        time_win = TimeWindow(start=temporal_start, end=temporal_end)
        req = AcquisitionRequest(
            investigation_id=investigation_id,
            provider_id="ais",
            asset_type=AssetType.VESSEL_TRACK,
            area_of_interest=BBoxAreaOfInterest(bbox=bbox_obj),
            time_window=time_win,
        )
        try:
            acq_result = provider.acquire(req)
            # Register acquired asset
            assets = target_registry.register_result(req, acq_result)
            if assets:
                ais_asset = assets[0]
                resolved_ais_id = ais_asset.id
                raw_records = parse_ais_records_from_artifact(Path(ais_asset.location))
        except AcquisitionConfigurationError:
            # Unconfigured adapter -> Report AIS_DATA_UNAVAILABLE without crashing
            res_id = f"cand-gen-{_sanitize(spill_id, 'spill')}-{_sanitize(investigation_id, 'inv')}"
            return (
                CandidateVesselGenerationResult(
                    id=res_id,
                    investigation_id=investigation_id,
                    source_estimate_id=source_estimate.id,
                    spill_detection_id=spill_id,
                    ais_asset_id=None,
                    derived_asset_id=None,
                    status=CandidateGenerationStatus.AIS_DATA_UNAVAILABLE,
                    source_time=source_time,
                    source_uncertainty_radius_km=uncertainty_radius_km,
                    temporal_window_start=temporal_start,
                    temporal_window_end=temporal_end,
                    spatial_query_bbox=spatial_bbox,
                    candidate_count=0,
                    total_vessels_checked=0,
                    candidates=[],
                    metadata={
                        "message": "Historical AIS data is unavailable. MARIS does not fabricate vessel tracks.",
                        "zero_fabrication_guarantee": True,
                    },
                ),
                None,
            )

    # 4. Check for empty AIS dataset
    if not raw_records:
        res_id = f"cand-gen-{_sanitize(spill_id, 'spill')}-{_sanitize(investigation_id, 'inv')}"
        return (
            CandidateVesselGenerationResult(
                id=res_id,
                investigation_id=investigation_id,
                source_estimate_id=source_estimate.id,
                spill_detection_id=spill_id,
                ais_asset_id=resolved_ais_id,
                derived_asset_id=None,
                status=CandidateGenerationStatus.NO_CANDIDATES_FOUND,
                source_time=source_time,
                source_uncertainty_radius_km=uncertainty_radius_km,
                temporal_window_start=temporal_start,
                temporal_window_end=temporal_end,
                spatial_query_bbox=spatial_bbox,
                candidate_count=0,
                total_vessels_checked=0,
                candidates=[],
                metadata={
                    "message": "AIS dataset contains zero position records.",
                    "zero_fabrication_guarantee": True,
                },
            ),
            None,
        )

    # 5. Process records and evaluate candidates
    # Count unique vessels across the dataset
    all_vessel_keys: set[str] = set()
    for rec in raw_records:
        if isinstance(rec, dict):
            key = rec.get("mmsi") or rec.get("imo") or rec.get("vessel_name")
            if key:
                all_vessel_keys.add(str(key).strip())

    total_vessels_checked = max(len(all_vessel_keys), 1 if raw_records else 0)

    # Group qualified observations by vessel identity
    # A candidate observation MUST satisfy:
    #   temporal_start <= timestamp <= temporal_end
    #   AND (distance <= uncertainty_radius_km OR inside_polygon OR distance <= uncertainty_radius_km + spatial_buffer_km)
    retained_by_vessel: dict[str, list[dict[str, Any]]] = {}

    max_allowed_dist_km = uncertainty_radius_km + spatial_buffer_km

    for idx, rec in enumerate(raw_records):
        if not isinstance(rec, dict):
            continue

        # Parse timestamp
        raw_ts = rec.get("timestamp")
        if not raw_ts:
            continue
        try:
            if isinstance(raw_ts, datetime):
                rec_dt = _utc(raw_ts)
            else:
                rec_dt = _utc(datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")))
        except Exception:
            continue

        # Temporal filter
        if not (temporal_start <= rec_dt <= temporal_end):
            continue

        # Parse coordinates
        raw_lat = rec.get("lat") if "lat" in rec else rec.get("latitude")
        raw_lon = rec.get("lon") if "lon" in rec else rec.get("longitude")
        if raw_lat is None or raw_lon is None:
            continue
        try:
            lat_f = float(raw_lat)
            lon_f = float(raw_lon)
        except (ValueError, TypeError):
            continue

        if not (math.isfinite(lat_f) and math.isfinite(lon_f)):
            continue
        if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0):
            continue

        # Distance to source center point (km)
        dist_m = _haversine_m(lon_f, lat_f, source_center_lon, source_center_lat)
        dist_km = dist_m / 1000.0

        # Point in polygon containment
        inside_poly = point_in_polygon(lon_f, lat_f, polygon_ring)

        # Retention check
        is_inside_zone = (dist_km <= uncertainty_radius_km) or inside_poly
        is_retained = is_inside_zone or (dist_km <= max_allowed_dist_km)

        if not is_retained:
            continue

        # Determine vessel identity key
        mmsi_val = str(rec.get("mmsi")).strip() if rec.get("mmsi") is not None else None
        imo_val = str(rec.get("imo")).strip() if rec.get("imo") is not None else None
        name_val = str(rec.get("vessel_name")).strip() if rec.get("vessel_name") is not None else None

        if mmsi_val:
            v_key = f"mmsi:{mmsi_val}"
        elif imo_val:
            v_key = f"imo:{imo_val}"
        elif name_val:
            v_key = f"name:{name_val}"
        else:
            v_key = f"unknown:{idx}"

        rec_augmented = dict(rec)
        rec_augmented["_parsed_dt"] = rec_dt
        rec_augmented["_parsed_lon"] = lon_f
        rec_augmented["_parsed_lat"] = lat_f
        rec_augmented["_dist_km"] = dist_km
        rec_augmented["_inside_zone"] = is_inside_zone

        retained_by_vessel.setdefault(v_key, []).append(rec_augmented)

    # 6. Assemble candidate vessel domain objects
    candidates: list[CandidateVessel] = []

    for v_key, obs_list in retained_by_vessel.items():
        # Sort chronologically
        obs_list.sort(key=lambda x: x["_parsed_dt"])

        # Determine closest point of approach (CPA) from actual observations
        cpa_obs = min(obs_list, key=lambda x: x["_dist_km"])

        min_dist_km = cpa_obs["_dist_km"]
        inside_any = any(x["_inside_zone"] for x in obs_list)
        dist_to_boundary = 0.0 if inside_any else max(0.0, min_dist_km - uncertainty_radius_km)

        cpa_dt = cpa_obs["_parsed_dt"]
        time_offset_h = (cpa_dt - source_time).total_seconds() / 3600.0

        # Identity resolution
        first_mmsi = next((str(x["mmsi"]).strip() for x in obs_list if x.get("mmsi") is not None), None)
        first_imo = next((str(x["imo"]).strip() for x in obs_list if x.get("imo") is not None), None)
        first_name = next((str(x["vessel_name"]).strip() for x in obs_list if x.get("vessel_name") is not None), None)

        primary_vessel_id = first_mmsi or first_imo or first_name or v_key
        cand_id = f"cand-{_sanitize(primary_vessel_id, 'vessel')}"

        # Kinematics at CPA (handling optional/malformed fields gracefully)
        sog_val: float | None = None
        raw_sog = cpa_obs.get("speed_over_ground") if "speed_over_ground" in cpa_obs else cpa_obs.get("sog")
        if raw_sog is not None:
            try:
                sog_f = float(raw_sog)
                if math.isfinite(sog_f) and sog_f >= 0.0:
                    sog_val = sog_f
            except (ValueError, TypeError):
                pass

        cog_val: float | None = None
        raw_cog = cpa_obs.get("course_over_ground") if "course_over_ground" in cpa_obs else cpa_obs.get("cog")
        if raw_cog is not None:
            try:
                cog_f = float(raw_cog)
                if math.isfinite(cog_f) and 0.0 <= cog_f < 360.0:
                    cog_val = cog_f
            except (ValueError, TypeError):
                pass

        heading_val: float | None = None
        raw_heading = cpa_obs.get("heading")
        if raw_heading is not None:
            try:
                h_f = float(raw_heading)
                if math.isfinite(h_f) and 0.0 <= h_f < 360.0:
                    heading_val = h_f
            except (ValueError, TypeError):
                pass

        nav_status = cpa_obs.get("navigation_status")
        if nav_status is not None:
            nav_status = str(nav_status).strip() or None

        # Build raw observed positions list
        raw_positions: list[VesselPosition] = []
        for x in obs_list:
            pt_sog: float | None = None
            raw_pt_sog = x.get("speed_over_ground") if "speed_over_ground" in x else x.get("sog")
            if raw_pt_sog is not None:
                try:
                    v = float(raw_pt_sog)
                    if math.isfinite(v) and v >= 0.0:
                        pt_sog = v
                except (ValueError, TypeError):
                    pass

            pt_cog: float | None = None
            raw_pt_cog = x.get("course_over_ground") if "course_over_ground" in x else x.get("cog")
            if raw_pt_cog is not None:
                try:
                    v = float(raw_pt_cog)
                    if math.isfinite(v) and 0.0 <= v < 360.0:
                        pt_cog = v
                except (ValueError, TypeError):
                    pass

            pt_heading: float | None = None
            raw_pt_heading = x.get("heading")
            if raw_pt_heading is not None:
                try:
                    v = float(raw_pt_heading)
                    if math.isfinite(v) and 0.0 <= v < 360.0:
                        pt_heading = v
                except (ValueError, TypeError):
                    pass

            raw_positions.append(
                VesselPosition(
                    timestamp=x["_parsed_dt"],
                    lon=x["_parsed_lon"],
                    lat=x["_parsed_lat"],
                    speed=pt_sog,
                    heading=pt_heading,
                    course=pt_cog,
                    metadata={
                        k: v for k, v in x.items()
                        if not k.startswith("_") and k not in {"lat", "lon", "timestamp"}
                    },
                )
            )


        cand = CandidateVessel(
            candidate_id=cand_id,
            vessel_id=primary_vessel_id,
            mmsi=first_mmsi,
            imo=first_imo,
            vessel_name=first_name,
            inside_source_zone=inside_any,
            min_distance_to_source_center_km=round(min_dist_km, 4),
            distance_to_zone_boundary_km=round(dist_to_boundary, 4),
            closest_position_lon=round(cpa_obs["_parsed_lon"], 7),
            closest_position_lat=round(cpa_obs["_parsed_lat"], 7),
            closest_position_time=cpa_dt,
            time_offset_from_source_hours=round(time_offset_h, 4),
            speed_over_ground=sog_val,
            course_over_ground=cog_val,
            heading=heading_val,
            navigation_status=nav_status,
            observed_positions_count=len(obs_list),
            raw_positions=raw_positions,
            metadata={
                "cpa_index": obs_list.index(cpa_obs),
                "identity_priority": "mmsi" if first_mmsi else ("imo" if first_imo else "name"),
            },
        )
        candidates.append(cand)

    # Sort candidates deterministically:
    # 1. inside_source_zone descending
    # 2. min_distance_to_source_center_km ascending
    # 3. abs(time_offset_from_source_hours) ascending
    # 4. candidate_id ascending
    candidates.sort(
        key=lambda c: (
            not c.inside_source_zone,
            c.min_distance_to_source_center_km,
            abs(c.time_offset_from_source_hours),
            c.candidate_id,
        )
    )

    status = (
        CandidateGenerationStatus.COMPLETED
        if candidates
        else CandidateGenerationStatus.NO_CANDIDATES_FOUND
    )

    # 7. Write GeoJSON artifact
    if output_dir is not None:
        target_dir = Path(output_dir)
    else:
        safe_inv = _sanitize(investigation_id, "investigation")
        safe_spill = _sanitize(spill_id, "spill")
        target_dir = (
            Path(settings.data_dir)
            / "derived"
            / safe_inv
            / "candidates"
            / safe_spill
        )
    target_dir.mkdir(parents=True, exist_ok=True)

    features: list[dict[str, Any]] = []

    # CPA points
    for c in candidates:
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [c.closest_position_lon, c.closest_position_lat],
            },
            "properties": {
                "feature_kind": "candidate_cpa",
                "candidate_id": c.candidate_id,
                "vessel_id": c.vessel_id,
                "mmsi": c.mmsi,
                "imo": c.imo,
                "vessel_name": c.vessel_name,
                "inside_source_zone": c.inside_source_zone,
                "min_distance_to_source_center_km": c.min_distance_to_source_center_km,
                "distance_to_zone_boundary_km": c.distance_to_zone_boundary_km,
                "closest_position_time": c.closest_position_time.isoformat(),
                "time_offset_from_source_hours": c.time_offset_from_source_hours,
                "speed_over_ground": c.speed_over_ground,
                "course_over_ground": c.course_over_ground,
                "heading": c.heading,
                "navigation_status": c.navigation_status,
                "observed_positions_count": c.observed_positions_count,
            },
        })

    # Observed trajectory features (strictly connecting genuine observed positions)
    for c in candidates:
        if len(c.raw_positions) >= 2:
            track_geom = {
                "type": "LineString",
                "coordinates": [[p.lon, p.lat] for p in c.raw_positions],
            }
        else:
            track_geom = {
                "type": "Point",
                "coordinates": [c.raw_positions[0].lon, c.raw_positions[0].lat],
            }
        features.append({
            "type": "Feature",
            "geometry": track_geom,
            "properties": {
                "feature_kind": "observed_vessel_track",
                "candidate_id": c.candidate_id,
                "vessel_id": c.vessel_id,
                "mmsi": c.mmsi,
                "point_count": len(c.raw_positions),
                "zero_fabrication": True,
            },
        })

    geojson_body = {
        "type": "FeatureCollection",
        "features": features,
    }
    geojson_path = target_dir / "candidate_vessels.geojson"
    geojson_path.write_text(json.dumps(geojson_body, indent=2) + "\n", encoding="utf-8")

    # 8. Register derived DOCUMENT asset
    now_utc = datetime.now(timezone.utc)
    res_id = f"cand-gen-{_sanitize(spill_id, 'spill')}-{_sanitize(investigation_id, 'inv')}"

    artifact = AcquiredArtifact(
        asset_type=AssetType.DOCUMENT,
        location=str(geojson_path.resolve()),
        source="candidate_vessel_generation_service",
        acquisition_time=now_utc,
        provenance=Provenance(
            product_id=source_estimate.id,
            retrieved_at=now_utc,
            processing_level="candidate_vessel_generation",
            notes=(
                f"Generated {len(candidates)} candidate vessels for D3 source zone. "
                f"Window=[{temporal_start.isoformat()} - {temporal_end.isoformat()}], "
                f"Buffer={spatial_buffer_km}km. Zero-fabrication guarantee."
            ),
            extra={
                "asset_type": "candidate_vessels",
                "source_estimate_id": source_estimate.id,
                "spill_detection_id": spill_id,
                "ais_asset_id": resolved_ais_id,
                "temporal_window_hours": temporal_window_hours,
                "spatial_buffer_km": spatial_buffer_km,
                "candidate_count": len(candidates),
                "total_vessels_checked": total_vessels_checked,
            },
        ),
        metadata={
            "asset_type": "candidate_vessels",
            "investigation_id": investigation_id,
            "spill_id": spill_id,
            "source_estimate_id": source_estimate.id,
            "candidate_count": len(candidates),
        },
    )

    derived_asset = target_registry.register(
        investigation_id, "candidate_vessel_generation_service", artifact
    )

    result = CandidateVesselGenerationResult(
        id=res_id,
        investigation_id=investigation_id,
        source_estimate_id=source_estimate.id,
        spill_detection_id=spill_id,
        ais_asset_id=resolved_ais_id,
        derived_asset_id=derived_asset.id,
        status=status,
        source_time=source_time,
        source_uncertainty_radius_km=uncertainty_radius_km,
        temporal_window_start=temporal_start,
        temporal_window_end=temporal_end,
        spatial_query_bbox=spatial_bbox,
        candidate_count=len(candidates),
        total_vessels_checked=total_vessels_checked,
        candidates=candidates,
        metadata={
            "temporal_window_hours": temporal_window_hours,
            "spatial_buffer_km": spatial_buffer_km,
            "zero_fabrication_guarantee": True,
            "artifact_path": str(geojson_path.resolve()),
        },
    )

    return result, derived_asset
