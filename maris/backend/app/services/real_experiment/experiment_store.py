"""MARIS Real-Experiment — Experiment Store.

SQLite-backed persistence for real-data attribution experiment runs and results.
Uses Python stdlib sqlite3; zero new dependencies.

The database is created automatically on first use at:
    <MARIS_DATA_DIR>/real_experiments.db

One experiment_store per process is managed through module-level singleton
(thread-safe via Python GIL + SQLite WAL mode).

Design principles:
- Schema is created idempotently with IF NOT EXISTS; safe to run multiple times.
- Each run is stored atomically; partial runs are not committed.
- Vessel features are stored as JSON blobs (no separate ORM table); the schema
  is intentionally simple and not intended to be queried analytically.
- No ORM is used; direct sqlite3 is preferred to keep the dependency footprint minimal.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings as default_settings
from app.services.real_experiment.experiment_runner import ExperimentResult


# ---------------------------------------------------------------------------
# Schema (idempotent)
# ---------------------------------------------------------------------------

_DDL = """\
CREATE TABLE IF NOT EXISTS experiment_runs (
    run_id                  TEXT PRIMARY KEY,
    satellite_product_id    TEXT NOT NULL,
    observation_time        TEXT NOT NULL,
    backtrack_hours         REAL NOT NULL,
    step_hours              REAL NOT NULL,
    model_version           TEXT NOT NULL,
    source_lon              REAL NOT NULL,
    source_lat              REAL NOT NULL,
    source_radius_m         REAL NOT NULL,
    source_zone_geojson     TEXT NOT NULL,  -- JSON
    backward_steps          TEXT NOT NULL,  -- JSON array
    vessels_json            TEXT NOT NULL,  -- JSON array of VesselFeatures.as_dict()
    era5_path               TEXT NOT NULL,
    cmems_path              TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    scientific_disclaimer   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiment_run_tags (
    run_id  TEXT NOT NULL REFERENCES experiment_runs(run_id) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (run_id, key)
);

CREATE TABLE IF NOT EXISTS synthetic_experiment_runs (
    run_id                  TEXT PRIMARY KEY,
    scenario_id             TEXT NOT NULL,
    seed                    INTEGER NOT NULL,
    observation_time        TEXT NOT NULL,
    backtrack_hours         REAL NOT NULL,
    step_hours              REAL NOT NULL,
    model_id                TEXT NOT NULL,
    model_type              TEXT NOT NULL,
    origin_lon              REAL NOT NULL,
    origin_lat              REAL NOT NULL,
    spill_area_m2           REAL NOT NULL,
    source_lon              REAL NOT NULL,
    source_lat              REAL NOT NULL,
    source_radius_m         REAL NOT NULL,
    source_zone_geojson     TEXT NOT NULL,
    backward_steps          TEXT NOT NULL,
    vessels_json            TEXT NOT NULL,
    ground_truth_vessel_id  TEXT NOT NULL,
    top_candidate_id        TEXT,
    attribution_match       INTEGER NOT NULL,
    created_at              TEXT NOT NULL,
    result_json             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluator_investigations (
    investigation_id            TEXT PRIMARY KEY,
    selected_image_id           TEXT NOT NULL,
    image_path                  TEXT NOT NULL,
    image_title                 TEXT NOT NULL,
    coordinates_json            TEXT NOT NULL,
    acquisition_timestamp       TEXT NOT NULL,
    wind_inputs_json            TEXT NOT NULL,
    current_inputs_json         TEXT NOT NULL,
    drift_parameters_json       TEXT NOT NULL,
    reconstructed_source_json   TEXT NOT NULL,
    eligible_vessel_ids_json    TEXT NOT NULL,
    relevant_vessels_data_json  TEXT NOT NULL,
    model_version               TEXT NOT NULL,
    candidate_probabilities_json TEXT NOT NULL,
    final_attribution_json      TEXT NOT NULL,
    created_at                  TEXT NOT NULL,
    full_result_json            TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class ExperimentStore:
    """Thread-safe SQLite store for real-data experiment results.

    Usage::

        store = ExperimentStore()   # uses default data dir
        store.save(result)
        runs = store.list_runs()
        run  = store.get_run("some-uuid")
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            db_path = Path(default_settings.data_dir) / "real_experiments.db"
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(self, result: ExperimentResult, tags: dict[str, str] | None = None) -> None:
        """Persist a completed experiment result.  Overwrites if run_id already exists."""
        row = self._to_row(result)
        tag_rows = [(result.run_id, k, v) for k, v in (tags or {}).items()]
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                conn.execute(
                    """
                    INSERT OR REPLACE INTO experiment_runs (
                        run_id, satellite_product_id, observation_time,
                        backtrack_hours, step_hours, model_version,
                        source_lon, source_lat, source_radius_m,
                        source_zone_geojson, backward_steps, vessels_json,
                        era5_path, cmems_path, created_at, scientific_disclaimer
                    ) VALUES (
                        :run_id, :satellite_product_id, :observation_time,
                        :backtrack_hours, :step_hours, :model_version,
                        :source_lon, :source_lat, :source_radius_m,
                        :source_zone_geojson, :backward_steps, :vessels_json,
                        :era5_path, :cmems_path, :created_at, :scientific_disclaimer
                    )
                    """,
                    row,
                )
                if tag_rows:
                    conn.execute("DELETE FROM experiment_run_tags WHERE run_id = ?", (result.run_id,))
                    conn.executemany(
                        "INSERT INTO experiment_run_tags (run_id, key, value) VALUES (?, ?, ?)",
                        tag_rows,
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    def get_run(self, run_id: str) -> ExperimentResult | None:
        """Return the full ExperimentResult for a run_id, or None if not found."""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "SELECT * FROM experiment_runs WHERE run_id = ?", (run_id,)
                )
                row = cursor.fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return self._from_row(dict(zip([d[0] for d in cursor.description], row)))

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return a list of run summaries (no backward_steps or vessel details) sorted by created_at desc."""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    SELECT run_id, satellite_product_id, observation_time,
                           backtrack_hours, model_version, source_lon, source_lat,
                           source_radius_m, created_at
                    FROM experiment_runs
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (max(1, min(limit, 200)),),
                )
                columns = [d[0] for d in cursor.description]
                rows = cursor.fetchall()
            finally:
                conn.close()
        return [dict(zip(columns, row)) for row in rows]

    def delete_run(self, run_id: str) -> bool:
        """Delete a run and its tags. Returns True if a row was deleted."""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute("DELETE FROM experiment_runs WHERE run_id = ?", (run_id,))
                conn.commit()
            finally:
                conn.close()
        return cursor.rowcount > 0

    def save_synthetic_run(self, result: dict[str, Any]) -> None:
        """Persist a synthetic experiment result dictionary."""
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN")
                conn.execute(
                    """
                    INSERT OR REPLACE INTO synthetic_experiment_runs (
                        run_id, scenario_id, seed, observation_time,
                        backtrack_hours, step_hours, model_id, model_type,
                        origin_lon, origin_lat, spill_area_m2,
                        source_lon, source_lat, source_radius_m,
                        source_zone_geojson, backward_steps, vessels_json,
                        ground_truth_vessel_id, top_candidate_id,
                        attribution_match, created_at, result_json
                    ) VALUES (
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?, ?
                    )
                    """,
                    (
                        result["run_id"],
                        result["scenario_id"],
                        result["seed"],
                        result["observation_time"],
                        result["backtrack_hours"],
                        result["step_hours"],
                        result["model_id"],
                        result["model_type"],
                        result["origin_lon"],
                        result["origin_lat"],
                        result["spill_area_m2"],
                        result["source_lon"],
                        result["source_lat"],
                        result["source_radius_m"],
                        json.dumps(result.get("source_zone_geojson", {})),
                        json.dumps(result.get("backward_steps", [])),
                        json.dumps(result.get("vessels", [])),
                        result["ground_truth_vessel_id"],
                        result.get("top_candidate_id"),
                        1 if result.get("attribution_match") else 0,
                        created_at,
                        json.dumps(result),
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    def get_synthetic_run(self, run_id: str) -> dict[str, Any] | None:
        """Fetch a full synthetic experiment result by run_id."""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "SELECT result_json FROM synthetic_experiment_runs WHERE run_id = ?",
                    (run_id,),
                )
                row = cursor.fetchone()
            finally:
                conn.close()
        if not row:
            return None
        return json.loads(row[0])

    def list_synthetic_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """List summary of synthetic runs sorted by created_at desc."""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    SELECT run_id, scenario_id, seed, observation_time,
                           backtrack_hours, model_id, model_type,
                           ground_truth_vessel_id, top_candidate_id,
                           attribution_match, created_at
                    FROM synthetic_experiment_runs
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (max(1, min(limit, 200)),),
                )
                columns = [d[0] for d in cursor.description]
                rows = cursor.fetchall()
            finally:
                conn.close()
        return [dict(zip(columns, row)) for row in rows]

    def save_evaluator_investigation(self, record: dict[str, Any]) -> None:
        """Persist a complete evaluator investigation run atomically."""
        investigation_id = record["investigation_id"]
        created_at = record.get("created_at") or datetime.now(timezone.utc).isoformat()
        if hasattr(created_at, "isoformat"):
            created_at = created_at.isoformat()

        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    INSERT INTO evaluator_investigations (
                        investigation_id, selected_image_id, image_path, image_title,
                        coordinates_json, acquisition_timestamp, wind_inputs_json,
                        current_inputs_json, drift_parameters_json, reconstructed_source_json,
                        eligible_vessel_ids_json, relevant_vessels_data_json, model_version,
                        candidate_probabilities_json, final_attribution_json, created_at,
                        full_result_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        investigation_id,
                        record.get("selected_image_id", ""),
                        record.get("image_path", ""),
                        record.get("image_title", ""),
                        json.dumps(record.get("coordinates", {})),
                        record.get("acquisition_timestamp", ""),
                        json.dumps(record.get("wind_inputs", {})),
                        json.dumps(record.get("current_inputs", {})),
                        json.dumps(record.get("drift_parameters", {})),
                        json.dumps(record.get("reconstructed_source", {})),
                        json.dumps(record.get("eligible_vessel_ids", [])),
                        json.dumps(record.get("relevant_vessels_data", [])),
                        record.get("model_version", ""),
                        json.dumps(record.get("candidate_probabilities", [])),
                        json.dumps(record.get("final_attribution", {})),
                        created_at,
                        json.dumps(record),
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    def get_evaluator_investigation(self, investigation_id: str) -> dict[str, Any] | None:
        """Fetch a full evaluator investigation record by ID."""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "SELECT full_result_json FROM evaluator_investigations WHERE investigation_id = ?",
                    (investigation_id,),
                )
                row = cursor.fetchone()
            finally:
                conn.close()
        if not row:
            return None
        return json.loads(row[0])

    def list_evaluator_investigations(self, limit: int = 50) -> list[dict[str, Any]]:
        """List summary of evaluator investigations sorted by created_at desc."""
        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute(
                    """
                    SELECT investigation_id, selected_image_id, image_title, image_path,
                           acquisition_timestamp, model_version, created_at,
                           candidate_probabilities_json, final_attribution_json
                    FROM evaluator_investigations
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (max(1, min(limit, 200)),),
                )
                rows = cursor.fetchall()
            finally:
                conn.close()
        results = []
        for row in rows:
            top_cand = None
            try:
                final_attr = json.loads(row[8]) if row[8] else {}
                top_cand = final_attr.get("top_candidate")
            except Exception:
                pass
            results.append({
                "investigation_id": row[0],
                "selected_image_id": row[1],
                "image_title": row[2],
                "image_path": row[3],
                "acquisition_timestamp": row[4],
                "model_version": row[5],
                "created_at": row[6],
                "top_candidate": top_cand,
            })
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(_DDL)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _to_row(result: ExperimentResult) -> dict[str, Any]:
        obs_time = (
            result.observation_time.isoformat()
            if hasattr(result.observation_time, "isoformat")
            else str(result.observation_time)
        )
        created_at = (
            result.created_at.isoformat()
            if hasattr(result.created_at, "isoformat")
            else str(result.created_at)
        )
        return {
            "run_id": result.run_id,
            "satellite_product_id": result.satellite_product_id,
            "observation_time": obs_time,
            "backtrack_hours": result.backtrack_hours,
            "step_hours": result.step_hours,
            "model_version": result.model_version,
            "source_lon": result.source_lon,
            "source_lat": result.source_lat,
            "source_radius_m": result.source_radius_m,
            "source_zone_geojson": json.dumps(result.source_zone_geojson),
            "backward_steps": json.dumps(result.backward_steps),
            "vessels_json": json.dumps([v.as_dict() for v in result.vessels]),
            "era5_path": result.era5_path,
            "cmems_path": result.cmems_path,
            "created_at": created_at,
            "scientific_disclaimer": result.scientific_disclaimer,
        }

    @staticmethod
    def _from_row(row: dict[str, Any]) -> ExperimentResult:
        from app.services.real_experiment.experiment_runner import VesselFeatures

        def _parse_dt(s: str) -> datetime:
            return datetime.fromisoformat(s)

        backward_steps = json.loads(row["backward_steps"])
        vessels_data = json.loads(row["vessels_json"])

        vessels = [
            VesselFeatures(
                vessel_id=v["vessel_id"],
                vessel_name=v.get("vessel_name"),
                mmsi=v.get("mmsi"),
                min_source_distance_km=v.get("min_source_distance_km"),
                temporal_overlap_hours=v.get("temporal_overlap_hours", 0.0),
                trajectory_overlap_fraction=v.get("trajectory_overlap_fraction", 0.0),
                heading_consistency=v.get("heading_consistency"),
                speed_consistency=v.get("speed_consistency"),
                ais_position_count=v.get("ais_position_count", 0),
                ais_coverage_fraction=v.get("ais_coverage_fraction", 0.0),
                evidence_consistency_score=v.get("evidence_consistency_score", 0.0),
                rank=v.get("rank", 0),
                has_meaningful_support=v.get("has_meaningful_support", True),
            )
            for v in vessels_data
        ]

        return ExperimentResult(
            run_id=row["run_id"],
            satellite_product_id=row["satellite_product_id"],
            observation_time=_parse_dt(row["observation_time"]),
            backtrack_hours=row["backtrack_hours"],
            step_hours=row["step_hours"],
            model_version=row["model_version"],
            source_lon=row["source_lon"],
            source_lat=row["source_lat"],
            source_radius_m=row["source_radius_m"],
            source_zone_geojson=json.loads(row["source_zone_geojson"]),
            backward_steps=backward_steps,
            vessels=vessels,
            era5_path=row["era5_path"],
            cmems_path=row["cmems_path"],
            created_at=_parse_dt(row["created_at"]),
            scientific_disclaimer=row["scientific_disclaimer"],
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store_instance: ExperimentStore | None = None
_store_lock = threading.Lock()


def get_experiment_store() -> ExperimentStore:
    """Return the module-level singleton ExperimentStore.  Thread-safe."""
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                _store_instance = ExperimentStore()
    return _store_instance
