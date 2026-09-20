"""MARIS AIS Vessel Database Service — Strict Data Provenance.

Manages the SQLite relational database at <MARIS_DATA_DIR>/ais_vessels.db.
Provides inspectable storage for:
- Real imported AIS data (e.g. NOAA MarineCadastre AccessAIS)
- Curated historical reference tracks (MANUAL_REFERENCE)
- Synthetic benchmark tracks (SYNTHETIC_BENCHMARK)
- External live AIS feeds (LIVE_AIS_PROVIDER)

Strict Provenance Rules:
- Synthetic or manually curated records are NEVER marked as real observations (is_real_observation = 0).
- NOAA/MarineCadastre is NEVER attributed to synthetic records.
- All records link to an explicit data_sources entry with full metadata.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings

# ---------------------------------------------------------------------------
# Strict Provenance Constants
# ---------------------------------------------------------------------------

PROVENANCE_NOAA_MARINECADASTRE = "NOAA_MARINECADASTRE"
PROVENANCE_SYNTHETIC_BENCHMARK = "SYNTHETIC_BENCHMARK"
PROVENANCE_MANUAL_REFERENCE = "MANUAL_REFERENCE"
PROVENANCE_LIVE_AIS_PROVIDER = "LIVE_AIS_PROVIDER"
PROVENANCE_UNVERIFIED_IMPORT = "UNVERIFIED_IMPORT"

VALID_PROVENANCES = {
    PROVENANCE_NOAA_MARINECADASTRE,
    PROVENANCE_SYNTHETIC_BENCHMARK,
    PROVENANCE_MANUAL_REFERENCE,
    PROVENANCE_LIVE_AIS_PROVIDER,
    PROVENANCE_UNVERIFIED_IMPORT,
}

# ---------------------------------------------------------------------------
# DDL Statements
# ---------------------------------------------------------------------------

_DDL = """\
CREATE TABLE IF NOT EXISTS data_sources (
    source_id               TEXT PRIMARY KEY,
    provider_name           TEXT NOT NULL,
    source_type             TEXT NOT NULL CHECK(source_type IN (
                                'NOAA_MARINECADASTRE',
                                'SYNTHETIC_BENCHMARK',
                                'MANUAL_REFERENCE',
                                'LIVE_AIS_PROVIDER',
                                'UNVERIFIED_IMPORT'
                            )),
    source_url              TEXT,
    acquisition_timestamp   TEXT NOT NULL,
    coverage_start          TEXT,
    coverage_end            TEXT,
    geographic_coverage     TEXT,
    is_real_observation     INTEGER NOT NULL DEFAULT 0 CHECK(is_real_observation IN (0, 1)),
    notes                   TEXT
);

CREATE TABLE IF NOT EXISTS import_batches (
    batch_id                TEXT PRIMARY KEY,
    source_id               TEXT NOT NULL REFERENCES data_sources(source_id),
    file_name               TEXT NOT NULL,
    imported_at             TEXT NOT NULL,
    total_rows              INTEGER NOT NULL,
    imported_rows           INTEGER NOT NULL,
    rejected_rows           INTEGER NOT NULL,
    rejection_reasons_json  TEXT NOT NULL,
    checksum                TEXT
);

CREATE TABLE IF NOT EXISTS vessels (
    vessel_id               TEXT PRIMARY KEY,
    mmsi                    TEXT NOT NULL,
    vessel_name             TEXT,
    imo                     TEXT,
    call_sign               TEXT,
    vessel_type             TEXT,
    length                  REAL,
    width                   REAL,
    draft                   REAL,
    flag_country            TEXT,
    source_id               TEXT NOT NULL REFERENCES data_sources(source_id),
    source_type             TEXT NOT NULL,
    is_real_observation     INTEGER NOT NULL DEFAULT 0 CHECK(is_real_observation IN (0, 1)),
    batch_id                TEXT REFERENCES import_batches(batch_id),
    created_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ais_positions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    vessel_id               TEXT NOT NULL REFERENCES vessels(vessel_id),
    mmsi                    TEXT NOT NULL,
    timestamp               TEXT NOT NULL,
    lat                     REAL NOT NULL,
    lon                     REAL NOT NULL,
    sog                     REAL,
    cog                     REAL,
    heading                 REAL,
    nav_status              TEXT,
    scene_id                TEXT,
    source_id               TEXT NOT NULL REFERENCES data_sources(source_id),
    source_type             TEXT NOT NULL,
    is_real_observation     INTEGER NOT NULL DEFAULT 0 CHECK(is_real_observation IN (0, 1)),
    batch_id                TEXT REFERENCES import_batches(batch_id),
    UNIQUE (mmsi, timestamp, lat, lon)
);

CREATE INDEX IF NOT EXISTS idx_ais_pos_mmsi_ts ON ais_positions(mmsi, timestamp);
CREATE INDEX IF NOT EXISTS idx_ais_pos_scene ON ais_positions(scene_id);
CREATE INDEX IF NOT EXISTS idx_ais_pos_time ON ais_positions(timestamp);
CREATE INDEX IF NOT EXISTS idx_ais_pos_geo ON ais_positions(lat, lon);
CREATE INDEX IF NOT EXISTS idx_ais_pos_source ON ais_positions(source_id);
CREATE INDEX IF NOT EXISTS idx_ais_pos_batch ON ais_positions(batch_id);
CREATE INDEX IF NOT EXISTS idx_vessels_mmsi ON vessels(mmsi);
CREATE INDEX IF NOT EXISTS idx_vessels_source ON vessels(source_id);
"""

_lock = threading.Lock()
_DB_PATH: Path | None = None


def get_ais_db_path() -> Path:
    """Return the absolute path to ais_vessels.db."""
    global _DB_PATH
    if _DB_PATH is None:
        db_dir = settings.data_dir
        db_dir.mkdir(parents=True, exist_ok=True)
        _DB_PATH = db_dir / "ais_vessels.db"
    return _DB_PATH


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection to the SQLite AIS database with WAL mode enabled."""
    path = db_path or get_ais_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _migrate_schema_if_needed(conn: sqlite3.Connection) -> None:
    """Migrate data_sources schema if created prior to UNVERIFIED_IMPORT provenance."""
    cur = conn.cursor()
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='data_sources';")
    row = cur.fetchone()
    if row and "UNVERIFIED_IMPORT" not in row["sql"]:
        cur.execute("PRAGMA foreign_keys=OFF;")
        cur.execute("""\
        CREATE TABLE data_sources_new (
            source_id               TEXT PRIMARY KEY,
            provider_name           TEXT NOT NULL,
            source_type             TEXT NOT NULL CHECK(source_type IN (
                                        'NOAA_MARINECADASTRE',
                                        'SYNTHETIC_BENCHMARK',
                                        'MANUAL_REFERENCE',
                                        'LIVE_AIS_PROVIDER',
                                        'UNVERIFIED_IMPORT'
                                    )),
            source_url              TEXT,
            acquisition_timestamp   TEXT NOT NULL,
            coverage_start          TEXT,
            coverage_end            TEXT,
            geographic_coverage     TEXT,
            is_real_observation     INTEGER NOT NULL DEFAULT 0 CHECK(is_real_observation IN (0, 1)),
            notes                   TEXT
        );
        """)
        cur.execute("INSERT INTO data_sources_new SELECT * FROM data_sources;")
        cur.execute("DROP TABLE data_sources;")
        cur.execute("ALTER TABLE data_sources_new RENAME TO data_sources;")
        cur.execute("PRAGMA foreign_keys=ON;")
        conn.commit()


def init_ais_database(db_path: Path | None = None) -> None:
    """Initialize the AIS vessel database schema idempotently and seed defaults."""
    with _lock:
        conn = get_connection(db_path)
        try:
            conn.executescript(_DDL)
            _migrate_schema_if_needed(conn)
            conn.commit()
        finally:
            conn.close()
        seed_benchmark_data(db_path)


# ---------------------------------------------------------------------------
# Strict Seeding of Reference and Benchmark Datasets
# ---------------------------------------------------------------------------

def seed_benchmark_data(db_path: Path | None = None) -> dict[str, int]:
    """Seed benchmark candidate vessels with strict provenance tracking.

    - Corsica 2018 reference vessels are classified as MANUAL_REFERENCE.
    - Synthetic regional scenarios are classified as SYNTHETIC_BENCHMARK.
    - All seeded benchmark records have is_real_observation = 0.
    """
    from app.services.real_experiment.evaluator_workflow import REFERENCE_OBSERVATIONS

    conn = get_connection(db_path)
    inserted_sources = 0
    inserted_vessels = 0
    inserted_positions = 0

    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        cur = conn.cursor()

        # 1. Register distinct data sources
        sources = [
            {
                "source_id": "src_manual_ref_corsica_2018",
                "provider_name": "BEA-Mer Incident Investigation / Curated Reconstruction",
                "source_type": PROVENANCE_MANUAL_REFERENCE,
                "source_url": "https://www.bea-mer.developpement-durable.gouv.fr/",
                "acquisition_timestamp": "2018-10-08T05:28:00Z",
                "coverage_start": "2018-10-07T20:00:00Z",
                "coverage_end": "2018-10-08T06:00:00Z",
                "geographic_coverage": "Cap Corse, Northern Mediterranean (WGS84 9.0-10.0E, 42.5-44.0N)",
                "is_real_observation": 0,
                "notes": (
                    "Curated historical collision benchmark reconstructed from BEA-Mer accident reports "
                    "for Ulysse and CSL Virginia. Curated for benchmark evaluation; not a live raw AIS broadcast."
                ),
            },
            {
                "source_id": "src_synth_arabian_sea_alpha",
                "provider_name": "MARIS Synthetic Scenario Generator",
                "source_type": PROVENANCE_SYNTHETIC_BENCHMARK,
                "source_url": "https://github.com/maris-project/maris",
                "acquisition_timestamp": "2025-03-15T05:42:00Z",
                "coverage_start": "2025-03-14T23:00:00Z",
                "coverage_end": "2025-03-15T06:00:00Z",
                "geographic_coverage": "Central Arabian Sea Shipping Lane (WGS84 65.5-67.0E, 15.0-16.5N)",
                "is_real_observation": 0,
                "notes": "Synthetic benchmark scenario for demonstration and offline algorithmic verification.",
            },
            {
                "source_id": "src_synth_arabian_sea_beta",
                "provider_name": "MARIS Synthetic Scenario Generator",
                "source_type": PROVENANCE_SYNTHETIC_BENCHMARK,
                "source_url": "https://github.com/maris-project/maris",
                "acquisition_timestamp": "2025-04-02T04:12:00Z",
                "coverage_start": "2025-04-01T22:00:00Z",
                "coverage_end": "2025-04-02T05:00:00Z",
                "geographic_coverage": "Northern Arabian Sea (WGS84 68.0-69.5E, 18.0-19.0N)",
                "is_real_observation": 0,
                "notes": "Synthetic benchmark scenario for demonstration.",
            },
            {
                "source_id": "src_synth_bay_of_bengal",
                "provider_name": "MARIS Synthetic Scenario Generator",
                "source_type": PROVENANCE_SYNTHETIC_BENCHMARK,
                "source_url": "https://github.com/maris-project/maris",
                "acquisition_timestamp": "2025-06-07T05:30:00Z",
                "coverage_start": "2025-06-06T23:00:00Z",
                "coverage_end": "2025-06-07T06:00:00Z",
                "geographic_coverage": "Bay of Bengal (WGS84 88.5-90.0E, 18.0-19.5N)",
                "is_real_observation": 0,
                "notes": "Synthetic benchmark scenario for demonstration.",
            },
        ]

        for s in sources:
            cur.execute(
                """\
                INSERT INTO data_sources (
                    source_id, provider_name, source_type, source_url, acquisition_timestamp,
                    coverage_start, coverage_end, geographic_coverage, is_real_observation, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    provider_name=excluded.provider_name,
                    source_type=excluded.source_type,
                    is_real_observation=excluded.is_real_observation,
                    notes=excluded.notes;
                """,
                (
                    s["source_id"],
                    s["provider_name"],
                    s["source_type"],
                    s["source_url"],
                    s["acquisition_timestamp"],
                    s["coverage_start"],
                    s["coverage_end"],
                    s["geographic_coverage"],
                    s["is_real_observation"],
                    s["notes"],
                ),
            )
            inserted_sources += 1

        # 2. Map reference scenes to sources
        scene_source_map = {
            "ref_corsica_2018": ("src_manual_ref_corsica_2018", PROVENANCE_MANUAL_REFERENCE),
            "ref_arabian_sea_alpha": ("src_synth_arabian_sea_alpha", PROVENANCE_SYNTHETIC_BENCHMARK),
            "ref_arabian_sea_beta": ("src_synth_arabian_sea_beta", PROVENANCE_SYNTHETIC_BENCHMARK),
            "ref_bay_of_bengal_gamma": ("src_synth_bay_of_bengal", PROVENANCE_SYNTHETIC_BENCHMARK),
        }

        for scene in REFERENCE_OBSERVATIONS:
            scene_id = scene["id"]
            if scene_id not in scene_source_map:
                continue
            source_id, source_type = scene_source_map[scene_id]

            for cand in scene.get("benchmark_candidates", []):
                vessel_id = cand["id"]
                mmsi = cand.get("mmsi") or f"GEN_{vessel_id}"
                v_name = cand.get("vessel_name")
                v_type = cand.get("vessel_type")

                # Insert vessel
                cur.execute(
                    """\
                    INSERT INTO vessels (
                        vessel_id, mmsi, vessel_name, imo, call_sign, vessel_type,
                        length, width, draft, flag_country, source_id, source_type,
                        is_real_observation, batch_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(vessel_id) DO UPDATE SET
                        mmsi=excluded.mmsi,
                        vessel_name=excluded.vessel_name,
                        vessel_type=excluded.vessel_type,
                        source_id=excluded.source_id,
                        source_type=excluded.source_type,
                        is_real_observation=excluded.is_real_observation;
                    """,
                    (
                        vessel_id,
                        mmsi,
                        v_name,
                        None,
                        None,
                        v_type,
                        None,
                        None,
                        None,
                        None,
                        source_id,
                        source_type,
                        0,
                        None,
                        now_iso,
                    ),
                )
                inserted_vessels += 1

                # Insert positions
                for pos in cand.get("positions", []):
                    cur.execute(
                        """\
                        INSERT INTO ais_positions (
                            vessel_id, mmsi, timestamp, lat, lon, sog, cog, heading,
                            nav_status, scene_id, source_id, source_type, is_real_observation, batch_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(mmsi, timestamp, lat, lon) DO UPDATE SET
                            sog=excluded.sog,
                            heading=excluded.heading,
                            scene_id=excluded.scene_id,
                            source_id=excluded.source_id,
                            source_type=excluded.source_type,
                            is_real_observation=excluded.is_real_observation;
                        """,
                        (
                            vessel_id,
                            mmsi,
                            pos["timestamp"],
                            pos["lat"],
                            pos["lon"],
                            pos.get("speed"),
                            pos.get("heading"),
                            pos.get("heading"),
                            None,
                            scene_id,
                            source_id,
                            source_type,
                            0,
                            None,
                        ),
                    )
                    inserted_positions += 1

        conn.commit()
    finally:
        conn.close()

    return {
        "sources": inserted_sources,
        "vessels": inserted_vessels,
        "positions": inserted_positions,
    }


# ---------------------------------------------------------------------------
# Query & Retrieval Functions
# ---------------------------------------------------------------------------

def get_candidates_for_scene(
    scene_id: str,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Retrieve all candidate vessels and their sequential positions for a scene from SQLite."""
    conn = get_connection(db_path)
    candidates: list[dict[str, Any]] = []
    try:
        cur = conn.cursor()
        cur.execute(
            """\
            SELECT DISTINCT v.vessel_id, v.mmsi, v.vessel_name, v.vessel_type,
                            v.source_id, v.source_type, v.is_real_observation,
                            ds.provider_name
            FROM vessels v
            JOIN ais_positions p ON v.vessel_id = p.vessel_id
            JOIN data_sources ds ON v.source_id = ds.source_id
            WHERE p.scene_id = ?
            ORDER BY v.vessel_name ASC;
            """,
            (scene_id,),
        )
        vessel_rows = cur.fetchall()

        for vr in vessel_rows:
            cur.execute(
                """\
                SELECT timestamp, lat, lon, sog, cog, heading, nav_status
                FROM ais_positions
                WHERE vessel_id = ? AND scene_id = ?
                ORDER BY timestamp ASC;
                """,
                (vr["vessel_id"], scene_id),
            )
            pos_rows = cur.fetchall()
            positions = [
                {
                    "timestamp": pr["timestamp"],
                    "lat": pr["lat"],
                    "lon": pr["lon"],
                    "speed": pr["sog"],
                    "heading": pr["heading"],
                    "cog": pr["cog"],
                    "nav_status": pr["nav_status"],
                }
                for pr in pos_rows
            ]

            candidates.append({
                "id": vr["vessel_id"],
                "vessel_id": vr["vessel_id"],
                "vessel_name": vr["vessel_name"],
                "mmsi": vr["mmsi"],
                "vessel_type": vr["vessel_type"],
                "source_id": vr["source_id"],
                "source_type": vr["source_type"],
                "is_real_observation": bool(vr["is_real_observation"]),
                "provider_name": vr["provider_name"],
                "positions": positions,
            })
    finally:
        conn.close()

    return candidates


def query_vessels_in_spatiotemporal_box(
    *,
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    t_start: datetime,
    t_end: datetime,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """SQL spatial-temporal prefilter for vessels with positions inside a geographic box and time window."""
    conn = get_connection(db_path)
    candidates: list[dict[str, Any]] = []
    t_start_iso = t_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    t_end_iso = t_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    try:
        cur = conn.cursor()
        # Find all distinct vessels with at least 1 position inside the spatial-temporal box
        cur.execute(
            """\
            SELECT DISTINCT v.vessel_id, v.mmsi, v.vessel_name, v.vessel_type,
                            v.source_id, v.source_type, v.is_real_observation,
                            ds.provider_name
            FROM vessels v
            JOIN ais_positions p ON v.vessel_id = p.vessel_id
            JOIN data_sources ds ON v.source_id = ds.source_id
            WHERE p.lat BETWEEN ? AND ?
              AND p.lon BETWEEN ? AND ?
              AND p.timestamp >= ?
              AND p.timestamp <= ?
            ORDER BY v.vessel_name ASC;
            """,
            (lat_min, lat_max, lon_min, lon_max, t_start_iso, t_end_iso),
        )
        vessel_rows = cur.fetchall()

        for vr in vessel_rows:
            # Fetch all positions for this vessel in the time window (even if some points are slightly outside box)
            cur.execute(
                """\
                SELECT timestamp, lat, lon, sog, cog, heading, nav_status
                FROM ais_positions
                WHERE vessel_id = ?
                  AND timestamp >= ?
                  AND timestamp <= ?
                ORDER BY timestamp ASC;
                """,
                (vr["vessel_id"], t_start_iso, t_end_iso),
            )
            pos_rows = cur.fetchall()
            positions = [
                {
                    "timestamp": pr["timestamp"],
                    "lat": pr["lat"],
                    "lon": pr["lon"],
                    "speed": pr["sog"],
                    "heading": pr["heading"],
                    "cog": pr["cog"],
                    "nav_status": pr["nav_status"],
                }
                for pr in pos_rows
            ]

            candidates.append({
                "id": vr["vessel_id"],
                "vessel_id": vr["vessel_id"],
                "vessel_name": vr["vessel_name"],
                "mmsi": vr["mmsi"],
                "vessel_type": vr["vessel_type"],
                "source_id": vr["source_id"],
                "source_type": vr["source_type"],
                "is_real_observation": bool(vr["is_real_observation"]),
                "provider_name": vr["provider_name"],
                "positions": positions,
            })
    finally:
        conn.close()

    return candidates


def get_vessel_by_identifier(
    identifier: str,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Look up a vessel by MMSI or vessel_id, returning full metadata and positions."""
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """\
            SELECT v.vessel_id, v.mmsi, v.vessel_name, v.imo, v.call_sign,
                   v.vessel_type, v.length, v.width, v.draft, v.flag_country,
                   v.source_id, v.source_type, v.is_real_observation,
                   ds.provider_name, ds.notes as source_notes
            FROM vessels v
            JOIN data_sources ds ON v.source_id = ds.source_id
            WHERE v.mmsi = ? OR v.vessel_id = ?
            LIMIT 1;
            """,
            (identifier, identifier),
        )
        row = cur.fetchone()
        if not row:
            return None

        vessel = dict(row)
        vessel["is_real_observation"] = bool(vessel["is_real_observation"])

        cur.execute(
            """\
            SELECT timestamp, lat, lon, sog, cog, heading, nav_status, scene_id
            FROM ais_positions
            WHERE vessel_id = ? OR mmsi = ?
            ORDER BY timestamp ASC;
            """,
            (vessel["vessel_id"], vessel["mmsi"]),
        )
        pos_rows = cur.fetchall()
        vessel["positions"] = [
            {
                "timestamp": pr["timestamp"],
                "lat": pr["lat"],
                "lon": pr["lon"],
                "speed": pr["sog"],
                "heading": pr["heading"],
                "cog": pr["cog"],
                "nav_status": pr["nav_status"],
                "scene_id": pr["scene_id"],
            }
            for pr in pos_rows
        ]
        return vessel
    finally:
        conn.close()


def get_database_statistics(db_path: Path | None = None) -> dict[str, Any]:
    """Return comprehensive summary counts and provenance breakdown from ais_vessels.db."""
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM data_sources;")
        total_sources = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM import_batches;")
        total_batches = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM vessels;")
        total_vessels = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM ais_positions;")
        total_positions = cur.fetchone()[0]

        cur.execute(
            """\
            SELECT source_type, is_real_observation, count(*) as cnt
            FROM vessels
            GROUP BY source_type, is_real_observation;
            """
        )
        vessel_provenance = [dict(r) for r in cur.fetchall()]

        cur.execute(
            """\
            SELECT source_type, is_real_observation, count(*) as cnt
            FROM ais_positions
            GROUP BY source_type, is_real_observation;
            """
        )
        position_provenance = [dict(r) for r in cur.fetchall()]

        cur.execute(
            """\
            SELECT sum(CASE WHEN is_real_observation = 1 THEN 1 ELSE 0 END) as real_cnt,
                   sum(CASE WHEN is_real_observation = 0 THEN 1 ELSE 0 END) as non_real_cnt
            FROM ais_positions;
            """
        )
        real_row = cur.fetchone()
        real_positions = real_row["real_cnt"] or 0
        non_real_positions = real_row["non_real_cnt"] or 0

        return {
            "total_sources": total_sources,
            "total_batches": total_batches,
            "total_vessels": total_vessels,
            "total_positions": total_positions,
            "real_positions": real_positions,
            "synthetic_or_manual_positions": non_real_positions,
            "vessel_provenance": vessel_provenance,
            "position_provenance": position_provenance,
        }
    finally:
        conn.close()
