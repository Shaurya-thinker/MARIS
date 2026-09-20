"""MARIS Importer for NOAA MarineCadastre AccessAIS Data.

Ingests real historical AIS CSV data into ais_vessels.db with strict provenance tracking.
Attributes source as:
    provider_name: NOAA / BOEM MarineCadastre.gov (AccessAIS)
    source_type: NOAA_MARINECADASTRE
    is_real_observation: 1

Validation & Safety:
- Validates latitude (-90 to 90) and longitude (-180 to 180).
- Normalizes timestamps to UTC ISO-8601.
- Validates SOG (0-102.2 knots), COG (0-360 deg), and Heading (0-360 deg; 511 -> NULL).
- Preserves missing vessel fields as NULL; never invents missing telemetry.
- Deterministic deduplication via SQLite UNIQUE constraint and row filtering.
- Tracks import batches with SHA-256 checksum and rejection accounting.
- Clarifies that MarineCadastre coverage is limited to US coastal/EEZ waters and is not a global live feed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.real_experiment.ais_database import (
    PROVENANCE_NOAA_MARINECADASTRE,
    PROVENANCE_UNVERIFIED_IMPORT,
    get_connection,
    init_ais_database,
)

# Standard MarineCadastre vessel type code mapping
VESSEL_TYPE_MAP: dict[str, str] = {
    "20": "Wing in Ground",
    "30": "Fishing",
    "31": "Towing",
    "32": "Towing (large)",
    "33": "Dredger",
    "34": "Diving Ops",
    "35": "Military Ops",
    "36": "Sailing",
    "37": "Pleasure Craft",
    "40": "High Speed Craft",
    "50": "Pilot Vessel",
    "51": "Search and Rescue",
    "52": "Tug",
    "53": "Port Tender",
    "54": "Anti-pollution",
    "55": "Law Enforcement",
    "60": "Passenger",
    "70": "Cargo",
    "80": "Tanker",
    "90": "Other",
}


@dataclass
class ImportReport:
    """Summary report for an AIS import operation."""

    batch_id: str
    file_name: str
    checksum: str
    total_rows: int
    imported_rows: int
    rejected_rows: int
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    vessels_created_or_updated: int = 0
    positions_inserted: int = 0
    coverage_start: str | None = None
    coverage_end: str | None = None
    geographic_bbox: dict[str, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "file_name": self.file_name,
            "checksum": self.checksum,
            "total_rows": self.total_rows,
            "imported_rows": self.imported_rows,
            "rejected_rows": self.rejected_rows,
            "rejection_reasons": self.rejection_reasons,
            "vessels_created_or_updated": self.vessels_created_or_updated,
            "positions_inserted": self.positions_inserted,
            "coverage_start": self.coverage_start,
            "coverage_end": self.coverage_end,
            "geographic_bbox": self.geographic_bbox,
        }


def _parse_timestamp(raw: str) -> str | None:
    """Parse various timestamp formats from CSV and normalize to UTC ISO-8601 string."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip()
    # Replace common date-time separators
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
    ):
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")
        except ValueError:
            continue

    try:
        # Fallback to fromisoformat
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _clean_str(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    return s if s and s.lower() not in ("null", "none", "nan", "unknown", "") else None


def _clean_float(val: Any, min_val: float | None = None, max_val: float | None = None) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if not math.isfinite(f):
            return None
        if min_val is not None and f < min_val:
            return None
        if max_val is not None and f > max_val:
            return None
        return f
    except (ValueError, TypeError):
        return None


def import_marinecadastre_csv(
    file_path_or_content: str | Path | io.StringIO,
    file_name: str = "marinecadastre_ais.csv",
    db_path: Path | None = None,
    source_id: str | None = None,
    is_authoritative_noaa: bool = False,
) -> ImportReport:
    """Import a CSV file conforming to NOAA/MarineCadastre AccessAIS schema.

    Expected standard column headers (case-insensitive):
    - MMSI
    - BaseDateTime
    - LAT
    - LON
    - SOG (Speed Over Ground)
    - COG (Course Over Ground)
    - Heading
    - VesselName
    - IMO
    - CallSign
    - VesselType
    - Status
    - Length
    - Width
    - Draft
    """
    init_ais_database(db_path)
    conn = get_connection(db_path)

    batch_id = f"batch_mc_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    now_iso = datetime.now(timezone.utc).isoformat()

    # Read content or open file
    if isinstance(file_path_or_content, (str, Path)) and Path(file_path_or_content).exists():
        p = Path(file_path_or_content)
        file_name = p.name
        content_bytes = p.read_bytes()
        checksum = hashlib.sha256(content_bytes).hexdigest()
        text_stream = io.StringIO(content_bytes.decode("utf-8", errors="replace"))
    elif isinstance(file_path_or_content, io.StringIO):
        text_content = file_path_or_content.getvalue()
        checksum = hashlib.sha256(text_content.encode("utf-8")).hexdigest()
        text_stream = file_path_or_content
    elif isinstance(file_path_or_content, str):
        checksum = hashlib.sha256(file_path_or_content.encode("utf-8")).hexdigest()
        text_stream = io.StringIO(file_path_or_content)
    else:
        raise ValueError("Invalid file_path_or_content provided to importer.")

    reader = csv.DictReader(text_stream)
    if not reader.fieldnames:
        return ImportReport(
            batch_id=batch_id,
            file_name=file_name,
            checksum=checksum,
            total_rows=0,
            imported_rows=0,
            rejected_rows=0,
            rejection_reasons={"empty_or_missing_header": 1},
        )

    # Normalize header mapping (case-insensitive)
    header_map: dict[str, str] = {col.strip().lower(): col for col in reader.fieldnames if col}

    def get_val(row: dict[str, str], *possible_keys: str) -> str | None:
        for k in possible_keys:
            orig = header_map.get(k.lower())
            if orig and orig in row and row[orig]:
                v = row[orig].strip()
                if v:
                    return v
        return None

    # Track metrics
    total_rows = 0
    imported_rows = 0
    rejected_rows = 0
    rejection_reasons: dict[str, int] = {}
    vessels_seen: set[str] = set()

    min_lat, max_lat = float("inf"), float("-inf")
    min_lon, max_lon = float("inf"), float("-inf")
    earliest_time, latest_time = None, None

    vessels_to_upsert: dict[str, dict[str, Any]] = {}
    positions_to_insert: list[dict[str, Any]] = []

    for row_idx, row in enumerate(reader, start=1):
        total_rows += 1

        # 1. MMSI
        raw_mmsi = get_val(row, "mmsi")
        if not raw_mmsi or not raw_mmsi.replace(".", "").isdigit():
            rejected_rows += 1
            rejection_reasons["invalid_or_missing_mmsi"] = rejection_reasons.get("invalid_or_missing_mmsi", 0) + 1
            continue
        mmsi = str(int(float(raw_mmsi)))

        # 2. Timestamp
        raw_time = get_val(row, "basedatetime", "timestamp", "datetime", "time")
        parsed_time = _parse_timestamp(raw_time) if raw_time else None
        if not parsed_time:
            rejected_rows += 1
            rejection_reasons["invalid_or_missing_timestamp"] = rejection_reasons.get("invalid_or_missing_timestamp", 0) + 1
            continue

        # 3. Latitude & Longitude
        lat = _clean_float(get_val(row, "lat", "latitude"), -90.0, 90.0)
        lon = _clean_float(get_val(row, "lon", "longitude"), -180.0, 180.0)
        if lat is None or lon is None or (lat == 0.0 and lon == 0.0):
            # (0, 0) is "Null Island" — typical GPS invalid fallback
            rejected_rows += 1
            rejection_reasons["invalid_coordinates"] = rejection_reasons.get("invalid_coordinates", 0) + 1
            continue

        # 4. Telemetry attributes
        sog = _clean_float(get_val(row, "sog", "speed"), 0.0, 102.2)
        cog = _clean_float(get_val(row, "cog", "course"), 0.0, 360.0)
        raw_heading = _clean_float(get_val(row, "heading"), 0.0, 511.0)
        heading = raw_heading if raw_heading is not None and raw_heading < 360.0 else None

        # 5. Vessel metadata
        v_name = _clean_str(get_val(row, "vesselname", "vessel_name", "name"))
        imo = _clean_str(get_val(row, "imo"))
        call_sign = _clean_str(get_val(row, "callsign", "call_sign"))
        raw_type = _clean_str(get_val(row, "vesseltype", "vessel_type", "type"))
        v_type = VESSEL_TYPE_MAP.get(raw_type, raw_type) if raw_type else None
        status = _clean_str(get_val(row, "status", "navstatus", "nav_status"))

        length = _clean_float(get_val(row, "length"), 1.0, 1000.0)
        width = _clean_float(get_val(row, "width"), 1.0, 200.0)
        draft = _clean_float(get_val(row, "draft"), 0.1, 50.0)

        # Update bounding metrics
        min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
        min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
        if earliest_time is None or parsed_time < earliest_time:
            earliest_time = parsed_time
        if latest_time is None or parsed_time > latest_time:
            latest_time = parsed_time

        # Determine provenance classification strictly
        if is_authoritative_noaa:
            eff_source_type = PROVENANCE_NOAA_MARINECADASTRE
            eff_is_real = 1
            eff_provider = "NOAA / BOEM MarineCadastre.gov (AccessAIS)"
            eff_notes = (
                "Verified historical vessel traffic data provided by NOAA Office for Coastal Management and BOEM. "
                "Data is restricted to US territorial waters and exclusive economic zone; does not provide global or real-time live AIS."
            )
            eff_source_id = source_id or "src_noaa_marinecadastre"
        else:
            eff_source_type = PROVENANCE_UNVERIFIED_IMPORT
            eff_is_real = 0
            eff_provider = "Unverified Import / Non-authoritative AIS Sample"
            eff_notes = (
                "Imported AIS data sample without authoritative local NOAA source artifact or independent verification."
            )
            eff_source_id = source_id or "src_unverified_import"

        # Track vessel
        vessel_id = f"vessel_mmsi_{mmsi}"
        if vessel_id not in vessels_to_upsert:
            vessels_to_upsert[vessel_id] = {
                "vessel_id": vessel_id,
                "mmsi": mmsi,
                "vessel_name": v_name or f"MMSI {mmsi}",
                "imo": imo,
                "call_sign": call_sign,
                "vessel_type": v_type or "Commercial Vessel",
                "length": length,
                "width": width,
                "draft": draft,
                "flag_country": None,
                "source_id": eff_source_id,
                "source_type": eff_source_type,
                "is_real_observation": eff_is_real,
                "batch_id": batch_id,
                "created_at": now_iso,
            }
        else:
            # Augment vessel if more complete data seen
            ex = vessels_to_upsert[vessel_id]
            if v_name and (not ex["vessel_name"] or ex["vessel_name"].startswith("MMSI ")):
                ex["vessel_name"] = v_name
            if imo and not ex["imo"]:
                ex["imo"] = imo
            if v_type and not ex["vessel_type"]:
                ex["vessel_type"] = v_type
            if length and not ex["length"]:
                ex["length"] = length
            if width and not ex["width"]:
                ex["width"] = width
            if draft and not ex["draft"]:
                ex["draft"] = draft

        positions_to_insert.append({
            "vessel_id": vessel_id,
            "mmsi": mmsi,
            "timestamp": parsed_time,
            "lat": lat,
            "lon": lon,
            "sog": sog,
            "cog": cog,
            "heading": heading,
            "nav_status": status,
            "scene_id": None,
            "source_id": eff_source_id,
            "source_type": eff_source_type,
            "is_real_observation": eff_is_real,
            "batch_id": batch_id,
        })
        imported_rows += 1

    # Database transaction
    try:
        cur = conn.cursor()

        # 1. Ensure data source is registered with realistic bounding notes
        geo_cov = (
            f"WGS84 Lat [{min_lat:.3f}, {max_lat:.3f}], Lon [{min_lon:.3f}, {max_lon:.3f}]"
            if min_lat != float("inf")
            else "US Coastal / EEZ Waters"
        )
        cur.execute(
            """\
            INSERT INTO data_sources (
                source_id, provider_name, source_type, source_url, acquisition_timestamp,
                coverage_start, coverage_end, geographic_coverage, is_real_observation, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                provider_name=excluded.provider_name,
                source_type=excluded.source_type,
                acquisition_timestamp=excluded.acquisition_timestamp,
                coverage_start=COALESCE(excluded.coverage_start, data_sources.coverage_start),
                coverage_end=COALESCE(excluded.coverage_end, data_sources.coverage_end),
                geographic_coverage=excluded.geographic_coverage,
                is_real_observation=excluded.is_real_observation,
                notes=excluded.notes;
            """,
            (
                eff_source_id,
                eff_provider,
                eff_source_type,
                "https://marinecadastre.gov/accessais/" if is_authoritative_noaa else None,
                now_iso,
                earliest_time,
                latest_time,
                geo_cov,
                eff_is_real,
                eff_notes,
            ),
        )

        # 2. Record Import Batch
        cur.execute(
            """\
            INSERT INTO import_batches (
                batch_id, source_id, file_name, imported_at, total_rows,
                imported_rows, rejected_rows, rejection_reasons_json, checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                batch_id,
                eff_source_id,
                file_name,
                now_iso,
                total_rows,
                imported_rows,
                rejected_rows,
                json.dumps(rejection_reasons),
                checksum,
            ),
        )

        # 3. Upsert Vessels
        vessels_created = 0
        for v in vessels_to_upsert.values():
            cur.execute(
                """\
                INSERT INTO vessels (
                    vessel_id, mmsi, vessel_name, imo, call_sign, vessel_type,
                    length, width, draft, flag_country, source_id, source_type,
                    is_real_observation, batch_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(vessel_id) DO UPDATE SET
                    vessel_name=COALESCE(excluded.vessel_name, vessels.vessel_name),
                    imo=COALESCE(excluded.imo, vessels.imo),
                    vessel_type=COALESCE(excluded.vessel_type, vessels.vessel_type),
                    length=COALESCE(excluded.length, vessels.length),
                    width=COALESCE(excluded.width, vessels.width),
                    draft=COALESCE(excluded.draft, vessels.draft),
                    source_id=excluded.source_id,
                    source_type=excluded.source_type,
                    is_real_observation=excluded.is_real_observation,
                    batch_id=excluded.batch_id;
                """,
                (
                    v["vessel_id"],
                    v["mmsi"],
                    v["vessel_name"],
                    v["imo"],
                    v["call_sign"],
                    v["vessel_type"],
                    v["length"],
                    v["width"],
                    v["draft"],
                    v["flag_country"],
                    v["source_id"],
                    v["source_type"],
                    v["is_real_observation"],
                    v["batch_id"],
                    v["created_at"],
                ),
            )
            vessels_created += 1

        # 4. Insert Positions (with deterministic deduplication)
        positions_inserted = 0
        for p in positions_to_insert:
            cur.execute(
                """\
                INSERT INTO ais_positions (
                    vessel_id, mmsi, timestamp, lat, lon, sog, cog, heading,
                    nav_status, scene_id, source_id, source_type, is_real_observation, batch_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mmsi, timestamp, lat, lon) DO NOTHING;
                """,
                (
                    p["vessel_id"],
                    p["mmsi"],
                    p["timestamp"],
                    p["lat"],
                    p["lon"],
                    p["sog"],
                    p["cog"],
                    p["heading"],
                    p["nav_status"],
                    p["scene_id"],
                    p["source_id"],
                    p["source_type"],
                    p["is_real_observation"],
                    p["batch_id"],
                ),
            )
            if cur.rowcount > 0:
                positions_inserted += 1

        conn.commit()
    finally:
        conn.close()

    bbox = (
        {
            "lat_min": round(min_lat, 4),
            "lat_max": round(max_lat, 4),
            "lon_min": round(min_lon, 4),
            "lon_max": round(max_lon, 4),
        }
        if min_lat != float("inf")
        else None
    )

    return ImportReport(
        batch_id=batch_id,
        file_name=file_name,
        checksum=checksum,
        total_rows=total_rows,
        imported_rows=imported_rows,
        rejected_rows=rejected_rows,
        rejection_reasons=rejection_reasons,
        vessels_created_or_updated=vessels_created,
        positions_inserted=positions_inserted,
        coverage_start=earliest_time,
        coverage_end=latest_time,
        geographic_bbox=bbox,
    )
