"""Comprehensive tests for MARIS AIS Vessel Database and Strict Provenance Tracking.

Covers:
1. Idempotent database initialization and schema creation.
2. Strict provenance values and constraints (is_real_observation = 0 for benchmark seeds).
3. NOAA MarineCadastre CSV import with field mapping and validation.
4. Missing field handling (null values preserved without hallucination).
5. Invalid row rejection (bad coordinates, invalid timestamps, null island).
6. Deterministic duplicate prevention.
7. SQL spatial-temporal prefiltering and scene retrieval.
8. Provider status discrimination with strict provenance.
9. Manual vessel investigation path (eligible, excluded with reasons, counterfactual).
"""

from __future__ import annotations

import io
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.real_experiment.ais_database import (
    PROVENANCE_LIVE_AIS_PROVIDER,
    PROVENANCE_MANUAL_REFERENCE,
    PROVENANCE_NOAA_MARINECADASTRE,
    PROVENANCE_SYNTHETIC_BENCHMARK,
    VALID_PROVENANCES,
    get_candidates_for_scene,
    get_connection,
    get_database_statistics,
    get_vessel_by_identifier,
    init_ais_database,
    query_vessels_in_spatiotemporal_box,
    seed_benchmark_data,
)
from app.services.real_experiment.evaluator_workflow import (
    determine_provider_status,
    investigate_manual_vessel,
    run_evaluator_investigation,
)
from app.services.real_experiment.import_marinecadastre import import_marinecadastre_csv


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Create an isolated temporary SQLite database for testing."""
    db_file = tmp_path / "ais_vessels_test.db"
    init_ais_database(db_file)
    return db_file


# ---------------------------------------------------------------------------
# 1. Schema, Indices, and Idempotent Initialization
# ---------------------------------------------------------------------------

def test_database_idempotent_init(temp_db: Path):
    """Verify tables and indices are created cleanly and safe to re-run multiple times."""
    # Re-run initialization to verify idempotence
    init_ais_database(temp_db)
    init_ais_database(temp_db)

    conn = get_connection(temp_db)
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {r["name"] for r in cur.fetchall()}
        assert "data_sources" in tables
        assert "import_batches" in tables
        assert "vessels" in tables
        assert "ais_positions" in tables

        cur.execute("SELECT name FROM sqlite_master WHERE type='index';")
        indices = {r["name"] for r in cur.fetchall()}
        assert "idx_ais_pos_mmsi_ts" in indices
        assert "idx_ais_pos_scene" in indices
        assert "idx_ais_pos_geo" in indices
        assert "idx_ais_pos_time" in indices
        assert "idx_vessels_mmsi" in indices
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Strict Provenance and Seed Data Classification
# ---------------------------------------------------------------------------

def test_seed_data_strict_provenance(temp_db: Path):
    """Ensure benchmark seeds NEVER claim to be real NOAA/MarineCadastre observations."""
    stats = get_database_statistics(temp_db)

    # Initial seeds must have ZERO real observations
    assert stats["real_positions"] == 0
    assert stats["synthetic_or_manual_positions"] > 0
    assert stats["total_vessels"] >= 10

    conn = get_connection(temp_db)
    try:
        cur = conn.cursor()
        # Verify data_sources table
        cur.execute("SELECT source_id, provider_name, source_type, is_real_observation FROM data_sources;")
        sources = cur.fetchall()
        for s in sources:
            assert s["source_type"] in VALID_PROVENANCES
            assert s["is_real_observation"] == 0
            # Ensure NOAA is not claimed as provider for synthetic/manual seeds
            assert "NOAA" not in s["provider_name"]
            assert "MarineCadastre" not in s["provider_name"]

        # Verify Corsica vessels are marked MANUAL_REFERENCE
        corsica_cands = get_candidates_for_scene("ref_corsica_2018", db_path=temp_db)
        assert len(corsica_cands) >= 2
        for c in corsica_cands:
            assert c["source_type"] == PROVENANCE_MANUAL_REFERENCE
            assert c["is_real_observation"] is False

        # Verify Arabian Sea vessels are marked SYNTHETIC_BENCHMARK
        arabian_cands = get_candidates_for_scene("ref_arabian_sea_alpha", db_path=temp_db)
        assert len(arabian_cands) >= 2
        for c in arabian_cands:
            assert c["source_type"] == PROVENANCE_SYNTHETIC_BENCHMARK
            assert c["is_real_observation"] is False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. NOAA MarineCadastre AccessAIS CSV Importer
# ---------------------------------------------------------------------------

def test_marinecadastre_importer_valid_and_missing_fields(temp_db: Path):
    """Verify real NOAA MarineCadastre CSV import preserves nulls without fabricating metadata."""
    sample_csv = """MMSI,BaseDateTime,LAT,LON,SOG,COG,Heading,VesselName,IMO,CallSign,VesselType,Status,Length,Width,Draft
367500111,2024-10-01T08:00:00Z,27.8500,-93.5000,12.4,180.0,179,GULF TRANSPORTER,IMO9876543,WDX1111,Tanker,under way using engine,250,44,14.0
367500111,2024-10-01T09:00:00Z,27.7000,-93.5000,12.2,180.0,180,GULF TRANSPORTER,IMO9876543,WDX1111,Tanker,under way using engine,250,44,14.0
368000222,2024-10-01T08:30:00Z,27.9000,-93.4000,0.2,45.0,511,ISLAND TUG,,WCE2222,52,at anchor,35,10,
"""
    report = import_marinecadastre_csv(
        io.StringIO(sample_csv),
        file_name="test_noaa_sample.csv",
        db_path=temp_db,
        is_authoritative_noaa=True,
    )

    assert report.total_rows == 3
    assert report.imported_rows == 3
    assert report.rejected_rows == 0
    assert report.positions_inserted == 3
    assert report.vessels_created_or_updated == 2

    # Verify vessel 1 (complete fields)
    v1 = get_vessel_by_identifier("367500111", db_path=temp_db)
    assert v1 is not None
    assert v1["vessel_name"] == "GULF TRANSPORTER"
    assert v1["imo"] == "IMO9876543"
    assert v1["vessel_type"] == "Tanker"
    assert v1["source_type"] == PROVENANCE_NOAA_MARINECADASTRE
    assert v1["is_real_observation"] is True
    assert len(v1["positions"]) == 2

    # Verify vessel 2 (missing IMO and Draft, mapped vessel type code 52 -> Tug)
    v2 = get_vessel_by_identifier("368000222", db_path=temp_db)
    assert v2 is not None
    assert v2["vessel_name"] == "ISLAND TUG"
    assert v2["imo"] is None  # Preserved as null, not invented
    assert v2["draft"] is None
    assert v2["vessel_type"] == "Tug"
    assert v2["source_type"] == PROVENANCE_NOAA_MARINECADASTRE
    assert v2["is_real_observation"] is True
    # Heading 511 in AIS represents unavailable -> None
    assert v2["positions"][0]["heading"] is None


def test_marinecadastre_importer_rejection_and_deduplication(temp_db: Path):
    """Verify invalid rows are rejected with explicit reasons and duplicates are ignored."""
    mixed_csv = """MMSI,BaseDateTime,LAT,LON,SOG,COG,Heading,VesselName,IMO,CallSign,VesselType,Status,Length,Width,Draft
367111222,2024-10-01T10:00:00Z,27.5000,-93.0000,10.0,90.0,90,TEST ALPHA,,,,,,,
367111222,2024-10-01T10:00:00Z,27.5000,-93.0000,10.0,90.0,90,TEST ALPHA,,,,,,,
,2024-10-01T10:00:00Z,27.5000,-93.0000,10.0,90.0,90,MISSING MMSI,,,,,,,
367999000,INVALID_DATE_TIME,27.5000,-93.0000,10.0,90.0,90,BAD TIME,,,,,,,
367999000,2024-10-01T10:00:00Z,125.0000,-93.0000,10.0,90.0,90,BAD LAT,,,,,,,
367999000,2024-10-01T10:00:00Z,0.0000,0.0000,10.0,90.0,90,NULL ISLAND,,,,,,,
"""
    report = import_marinecadastre_csv(
        io.StringIO(mixed_csv),
        file_name="mixed_sample.csv",
        db_path=temp_db,
    )

    assert report.total_rows == 6
    assert report.imported_rows == 2  # Row 1 and Row 2 (the duplicate row)
    assert report.rejected_rows == 4  # 4 rows rejected for invalid data
    assert report.positions_inserted == 1  # Row 2 deduplicated by SQLite constraint

    assert "invalid_or_missing_mmsi" in report.rejection_reasons
    assert "invalid_or_missing_timestamp" in report.rejection_reasons
    assert "invalid_coordinates" in report.rejection_reasons


# ---------------------------------------------------------------------------
# 4. Spatio-Temporal SQL Prefiltering
# ---------------------------------------------------------------------------

def test_spatiotemporal_sql_prefiltering(temp_db: Path):
    """Verify SQL bounding box and time window prefilter returns correct candidate vessels."""
    # Seed MarineCadastre vessel in Gulf of Mexico on 2024-10-01
    sample_csv = """MMSI,BaseDateTime,LAT,LON,SOG,COG,Heading,VesselName,IMO,CallSign,VesselType,Status,Length,Width,Draft
367777888,2024-10-01T14:00:00Z,28.2000,-93.8000,14.0,120.0,120,GULF RUNNER,IMO9111222,,Cargo,,200,32,10.5
"""
    import_marinecadastre_csv(io.StringIO(sample_csv), db_path=temp_db)

    # 1. Query matching spatial-temporal window
    t_start = datetime(2024, 10, 1, 12, 0, tzinfo=timezone.utc)
    t_end = datetime(2024, 10, 1, 16, 0, tzinfo=timezone.utc)
    res = query_vessels_in_spatiotemporal_box(
        lat_min=28.0,
        lat_max=28.5,
        lon_min=-94.0,
        lon_max=-93.5,
        t_start=t_start,
        t_end=t_end,
        db_path=temp_db,
    )
    assert len(res) == 1
    assert res[0]["mmsi"] == "367777888"

    # 2. Query outside spatial window
    res_far = query_vessels_in_spatiotemporal_box(
        lat_min=10.0,
        lat_max=12.0,
        lon_min=60.0,
        lon_max=65.0,
        t_start=t_start,
        t_end=t_end,
        db_path=temp_db,
    )
    assert len(res_far) == 0


# ---------------------------------------------------------------------------
# 5. Provider Status Discrimination with Provenance
# ---------------------------------------------------------------------------

def test_provider_status_discrimination_strict():
    """Verify required provider statuses: LIVE_AIS, OBSERVED_ARCHIVE, SYNTHETIC_BENCHMARK, MANUAL_REFERENCE, etc."""
    # 1. No provider and 0 found
    stat, _ = determine_provider_status(has_live_provider=False, total_found=0, eligible_count=0)
    assert stat == "NO_PROVIDER"

    # 2. Candidates found but 0 eligible
    stat, _ = determine_provider_status(has_live_provider=True, total_found=3, eligible_count=0)
    assert stat == "NO_ELIGIBLE_VESSELS"

    # 3. Insufficient coverage
    stat, _ = determine_provider_status(has_live_provider=False, total_found=2, eligible_count=2, insufficient_coverage=True)
    assert stat == "INSUFFICIENT_COVERAGE"

    # 4. Manual reference benchmark
    stat, _ = determine_provider_status(has_live_provider=False, total_found=4, eligible_count=2, provenance_source=PROVENANCE_MANUAL_REFERENCE)
    assert stat == "MANUAL_REFERENCE"

    # 5. Synthetic benchmark
    stat, _ = determine_provider_status(has_live_provider=False, total_found=3, eligible_count=2, provenance_source=PROVENANCE_SYNTHETIC_BENCHMARK)
    assert stat == "SYNTHETIC_BENCHMARK"

    # 6. Observed archive (NOAA MarineCadastre)
    stat, _ = determine_provider_status(has_live_provider=False, total_found=5, eligible_count=3, provenance_source=PROVENANCE_NOAA_MARINECADASTRE)
    assert stat == "OBSERVED_ARCHIVE"

    # 7. Live AIS stream
    stat, _ = determine_provider_status(has_live_provider=True, total_found=5, eligible_count=3, provenance_source=PROVENANCE_LIVE_AIS_PROVIDER)
    assert stat == "LIVE_AIS"


# ---------------------------------------------------------------------------
# 6. Manual Vessel Investigation Path
# ---------------------------------------------------------------------------

def test_manual_vessel_investigation_eligible_and_excluded(temp_db: Path):
    """Verify manual vessel lookup by MMSI or ID executes validation without fabricating attribution for out-of-corridor vessels."""
    obs_time = datetime(2018, 10, 8, 5, 28, tzinfo=timezone.utc)

    # 1. Eligible candidate: MV Ulysse (crossing collision corridor)
    res_ulysse = investigate_manual_vessel(
        vessel_identifier="228308800",
        observation_lon=9.47833,
        observation_lat=43.24833,
        observation_time=obs_time,
        wind_speed_ms=7.2,
        wind_direction_deg=235.0,
        current_speed_ms=0.22,
        current_direction_deg=35.0,
        corridor_km=30.0,
        backtrack_hours=8.0,
        db_path=temp_db,
    )
    assert res_ulysse["found"] is True
    assert res_ulysse["is_eligible"] is True
    assert res_ulysse["eligibility_status"] == "ELIGIBLE"
    assert res_ulysse["attribution"] is not None
    assert res_ulysse["attribution"]["model_probability"] > 0.5
    assert res_ulysse["vessel_metadata"]["source_type"] == PROVENANCE_MANUAL_REFERENCE
    assert res_ulysse["vessel_metadata"]["is_real_observation"] is False

    # 2. Excluded candidate: Ligurian Breeze (far outside corridor)
    res_distant = investigate_manual_vessel(
        vessel_identifier="247998877",
        observation_lon=9.47833,
        observation_lat=43.24833,
        observation_time=obs_time,
        wind_speed_ms=7.2,
        wind_direction_deg=235.0,
        current_speed_ms=0.22,
        current_direction_deg=35.0,
        corridor_km=25.0,
        backtrack_hours=8.0,
        db_path=temp_db,
    )
    assert res_distant["found"] is True
    assert res_distant["is_eligible"] is False
    assert res_distant["eligibility_status"] == "EXCLUDED"
    assert res_distant["attribution"] is None  # Standard attribution NOT run for excluded vessels
    assert len(res_distant["exclusion_reasons"]) > 0
    assert "OUTSIDE_CORRIDOR" in res_distant["exclusion_reasons"][0]

    # 3. Counterfactual mode: allowed when explicitly requested
    res_cf = investigate_manual_vessel(
        vessel_identifier="247998877",
        observation_lon=9.47833,
        observation_lat=43.24833,
        observation_time=obs_time,
        wind_speed_ms=7.2,
        wind_direction_deg=235.0,
        current_speed_ms=0.22,
        current_direction_deg=35.0,
        corridor_km=25.0,
        backtrack_hours=8.0,
        db_path=temp_db,
        force_counterfactual=True,
    )
    assert res_cf["is_eligible"] is False
    assert "counterfactual_attribution" in res_cf
    assert "DOCUMENTED COUNTERFACTUAL" in res_cf["counterfactual_attribution"]["notice"]

    # 4. Unknown MMSI lookup
    res_unknown = investigate_manual_vessel(
        vessel_identifier="999000999",
        observation_lon=9.47833,
        observation_lat=43.24833,
        observation_time=obs_time,
        wind_speed_ms=7.2,
        wind_direction_deg=235.0,
        current_speed_ms=0.22,
        current_direction_deg=35.0,
        db_path=temp_db,
    )
    assert res_unknown["found"] is False
    assert "not found" in res_unknown["error"]
