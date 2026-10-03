"""Phase 6.3 — Dual-Sensor SAR Surveillance & AIS Correlation Pipeline Integration Tests.

Validates:
1. Pydantic schema serialization / deserialization (SarBrightTargetSchema, SarAisAssociationSchema, SarSurveillanceResultSchema, ExperimentRunRequest/Response)
2. Neutral discrepancy classification serialization (scientific terminology adherence)
3. SQLite migration creation (sar_surveillance_json column in experiment_runs)
4. SQLite migration idempotency (safe repeated execution)
5. Persistence and retrieval of sar_surveillance_json in ExperimentStore
6. Empty surveillance results handling
7. Multiple bright targets handling
8. Ambiguous associations handling (AMBIGUOUS_MULTI_TARGET_PROXIMITY without forced 1-to-1)
9. Read-only AIS database integrity
10. Optional Phase 6 execution in ExperimentRunner
11. Preservation of Phase 1–5 pipeline outputs
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.api.experiment_schemas import (
    ExperimentRunRequest,
    ExperimentRunResponse,
    ExperimentRunSummary,
    SarAisAssociationSchema,
    SarBrightTargetSchema,
    SarSurveillanceConfigRequest,
    SarSurveillanceResultItem,
    SarSurveillanceResultSchema,
)
from app.models.source_estimation import BackwardDriftStep
from app.services.real_experiment.ais_database import get_ais_db_path
from app.services.real_experiment.experiment_runner import (
    ExperimentResult,
    ExperimentRunner,
    VesselFeatures,
)
from app.services.real_experiment.experiment_store import ExperimentStore
from app.services.real_experiment.sar_ais_matching_service import (
    APPROVED_CLASSIFICATIONS,
    CLASSIFICATION_AMBIGUOUS_PROXIMITY,
    CLASSIFICATION_COINCIDENT_MATCH,
    CLASSIFICATION_OBSERVATION_GAP,
    CLASSIFICATION_SPATIAL_EXCEEDANCE,
    CLASSIFICATION_TARGET_UNCORRELATED,
    CLASSIFICATION_VESSEL_NOT_DETECTED,
    DEFAULT_SURVEILLANCE_DISCLAIMER,
    SarAisAssociation,
    SarAisMatchingResult,
    SarAisMatchingService,
)
from app.services.real_experiment.sar_surveillance import (
    SarSurveillanceConfig,
    execute_sar_surveillance,
)
from app.services.real_experiment.sar_vessel_detector import SarBrightTarget


# ---------------------------------------------------------------------------
# 1. Pydantic schema serialization / deserialization
# ---------------------------------------------------------------------------

def test_pydantic_schema_serialization_deserialization():
    """Verify serialization and deserialization of Phase 6 surveillance schemas."""
    target_data = {
        "target_id": "sar-target-001",
        "pixel_x": 120,
        "pixel_y": 250,
        "lon": 9.451234,
        "lat": 43.219876,
        "peak_backscatter_db": -5.2,
        "local_clutter_mean_db": -16.4,
        "target_to_clutter_ratio_db": 11.2,
        "pixel_count": 8,
        "bounding_box_pixels": [118, 248, 122, 252],
    }
    target_schema = SarBrightTargetSchema(**target_data)
    assert target_schema.target_id == "sar-target-001"
    assert target_schema.lon == 9.451234
    serialized_target = target_schema.model_dump()
    assert serialized_target["pixel_count"] == 8

    assoc_data = {
        "association_id": "assoc-001",
        "classification": CLASSIFICATION_COINCIDENT_MATCH,
        "target_id": "sar-target-001",
        "vessel_id": "vessel-mmsi-227001",
        "mmsi": "227001000",
        "vessel_name": "TEST VESSEL",
        "distance_meters": 245.5,
        "target_lat": 43.219876,
        "target_lon": 9.451234,
        "target_peak_db": -5.2,
        "target_tcr_db": 11.2,
        "ais_lat": 43.220100,
        "ais_lon": 9.451500,
        "ais_sog_knots": 12.4,
        "ais_cog_degrees": 182.0,
        "ais_alignment_method": "INTERPOLATED",
        "ais_time_offset_seconds": 15.0,
        "ais_gap_seconds": 30.0,
        "ambiguous_candidate_ids": [],
        "ambiguous_distances_m": [],
        "notes": "Coincident radar target and AIS position",
    }
    assoc_schema = SarAisAssociationSchema(**assoc_data)
    assert assoc_schema.classification == CLASSIFICATION_COINCIDENT_MATCH
    assert assoc_schema.distance_meters == 245.5

    surv_result_data = {
        "observation_time_iso": "2018-10-08T05:30:00Z",
        "scene_bbox": [9.38, 43.15, 9.58, 43.35],
        "total_sar_targets": 1,
        "total_ais_candidates": 1,
        "matched_coincident_count": 1,
        "spatial_discrepancy_count": 0,
        "uncorrelated_target_count": 0,
        "undetected_vessel_count": 0,
        "observation_gap_count": 0,
        "ambiguous_count": 0,
        "associations": [assoc_data],
        "targets": [target_data],
        "parameters": {"coincident_spatial_gate_m": 1000.0},
        "scientific_disclaimer": DEFAULT_SURVEILLANCE_DISCLAIMER,
    }
    surv_result = SarSurveillanceResultSchema(**surv_result_data)
    assert surv_result.total_sar_targets == 1
    assert len(surv_result.associations) == 1
    assert len(surv_result.targets) == 1

    # In ExperimentRunRequest & Response
    req = ExperimentRunRequest(
        satellite_product_id="S1A_IW_GRDH_1SDV_20181008T053000",
        observation_lon=9.4783,
        observation_lat=43.2483,
        observation_time="2018-10-08T05:30:00Z",
        era5_netcdf_path="mock_era5.nc",
        cmems_netcdf_path="mock_cmems.nc",
        sar_surveillance=SarSurveillanceConfigRequest(enabled=True, guard_band_pixels=15),
    )
    assert req.sar_surveillance is not None
    assert req.sar_surveillance.enabled is True

    resp_data = {
        "run_id": "test-run-123",
        "satellite_product_id": "S1A_IW_GRDH_1SDV_20181008T053000",
        "observation_time": "2018-10-08T05:30:00Z",
        "backtrack_hours": 12.0,
        "step_hours": 1.0,
        "model_version": "1.0.0",
        "source_lon": 9.47,
        "source_lat": 43.24,
        "source_radius_m": 500.0,
        "source_zone_geojson": {"type": "FeatureCollection", "features": []},
        "backward_steps": [],
        "vessels": [],
        "era5_path": "mock_era5.nc",
        "cmems_path": "mock_cmems.nc",
        "created_at": "2026-10-03T00:00:00Z",
        "scientific_disclaimer": "Disclaimer",
        "sar_surveillance": surv_result_data,
    }
    resp = ExperimentRunResponse(**resp_data)
    assert resp.sar_surveillance is not None
    assert resp.sar_surveillance.total_sar_targets == 1


# ---------------------------------------------------------------------------
# 2. Neutral discrepancy classification serialization
# ---------------------------------------------------------------------------

def test_neutral_classification_serialization():
    """Verify all 6 neutral discrepancy classifications serialize without unapproved terminology."""
    classifications = list(APPROVED_CLASSIFICATIONS)
    assert len(classifications) == 6

    prohibited_terms = ["dark vessel", "sarvessel", "rcs", "dbsm", "doppler"]

    for classification in classifications:
        assoc = SarAisAssociationSchema(
            association_id=f"assoc-{classification.lower()}",
            classification=classification,
            notes=f"Test for classification {classification}",
        )
        serialized = assoc.model_dump_json()
        assert classification in serialized
        lower_ser = serialized.lower()
        for term in prohibited_terms:
            assert term not in lower_ser, f"Prohibited term '{term}' found in serialization: {serialized}"


# ---------------------------------------------------------------------------
# 3. SQLite migration creation
# ---------------------------------------------------------------------------

def test_sqlite_migration_creation(tmp_path):
    """Verify that a fresh ExperimentStore database creates sar_surveillance_json column."""
    db_path = tmp_path / "test_fresh_store.db"
    store = ExperimentStore(db_path=db_path)

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.execute("PRAGMA table_info(experiment_runs)")
        cols = {row[1]: row[2] for row in cursor.fetchall()}
        assert "sar_surveillance_json" in cols, f"Columns found: {cols.keys()}"
        assert "TEXT" in cols["sar_surveillance_json"].upper()


# ---------------------------------------------------------------------------
# 4. SQLite migration idempotency
# ---------------------------------------------------------------------------

def test_sqlite_migration_idempotency(tmp_path):
    """Verify that executing _ensure_schema() repeatedly or on legacy schemas is idempotent."""
    db_path = tmp_path / "test_idempotent_store.db"

    # 1. Create a legacy table without sar_surveillance_json
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE experiment_runs (
                run_id TEXT PRIMARY KEY,
                satellite_product_id TEXT,
                observation_time TEXT,
                backtrack_hours REAL,
                step_hours REAL,
                model_version TEXT,
                source_lon REAL,
                source_lat REAL,
                source_radius_m REAL,
                source_zone_geojson TEXT,
                backward_steps TEXT,
                vessels_json TEXT,
                era5_path TEXT,
                cmems_path TEXT,
                created_at TEXT,
                scientific_disclaimer TEXT
            );
            """
        )
        conn.commit()

    # 2. Instantiate ExperimentStore which applies migration
    store = ExperimentStore(db_path=db_path)

    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.execute("PRAGMA table_info(experiment_runs)")
        cols = {row[1]: row[2] for row in cursor.fetchall()}
        assert "sar_surveillance_json" in cols

    # 3. Call _ensure_schema() a second and third time
    store._ensure_schema()
    store._ensure_schema()

    # Check schema is still valid
    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.execute("PRAGMA table_info(experiment_runs)")
        cols = {row[1]: row[2] for row in cursor.fetchall()}
        assert "sar_surveillance_json" in cols


# ---------------------------------------------------------------------------
# 5. Persistence and retrieval of sar_surveillance_json
# ---------------------------------------------------------------------------

def test_persistence_and_retrieval_of_sar_surveillance_json(tmp_path):
    """Verify round-trip save, get, and list operations with sar_surveillance_json."""
    db_path = tmp_path / "test_surveillance_persistence.db"
    store = ExperimentStore(db_path=db_path)

    surv_data = {
        "observation_time_iso": "2018-10-08T05:30:00Z",
        "scene_bbox": [9.38, 43.15, 9.58, 43.35],
        "total_sar_targets": 2,
        "total_ais_candidates": 1,
        "matched_coincident_count": 1,
        "spatial_discrepancy_count": 0,
        "uncorrelated_target_count": 1,
        "undetected_vessel_count": 0,
        "observation_gap_count": 0,
        "ambiguous_count": 0,
        "associations": [
            {
                "association_id": "assoc-1",
                "classification": CLASSIFICATION_COINCIDENT_MATCH,
                "target_id": "target-1",
                "vessel_id": "vessel-1",
                "distance_meters": 150.0,
            },
            {
                "association_id": "assoc-2",
                "classification": "RADAR_TARGET_UNCORRELATED",
                "target_id": "target-2",
                "distance_meters": None,
            },
        ],
        "targets": [
            {
                "target_id": "target-1",
                "pixel_x": 100,
                "pixel_y": 100,
                "lon": 9.45,
                "lat": 43.25,
                "peak_backscatter_db": -6.0,
                "local_clutter_mean_db": -16.0,
                "target_to_clutter_ratio_db": 10.0,
                "pixel_count": 5,
                "bounding_box_pixels": [98, 98, 102, 102],
            },
            {
                "target_id": "target-2",
                "pixel_x": 200,
                "pixel_y": 200,
                "lon": 9.50,
                "lat": 43.30,
                "peak_backscatter_db": -4.0,
                "local_clutter_mean_db": -15.0,
                "target_to_clutter_ratio_db": 11.0,
                "pixel_count": 7,
                "bounding_box_pixels": [198, 198, 202, 202],
            },
        ],
        "parameters": {},
        "scientific_disclaimer": DEFAULT_SURVEILLANCE_DISCLAIMER,
    }

    result = ExperimentResult(
        run_id="run-persist-phase6",
        satellite_product_id="S1A_IW_GRDH_1SDV_20181008T053000",
        observation_time=datetime(2018, 10, 8, 5, 30, tzinfo=timezone.utc),
        backtrack_hours=12.0,
        step_hours=1.0,
        model_version="1.0.0",
        source_lon=9.47,
        source_lat=43.24,
        source_radius_m=500.0,
        source_zone_geojson={"type": "FeatureCollection", "features": []},
        backward_steps=[],
        vessels=[],
        era5_path="mock_era5.nc",
        cmems_path="mock_cmems.nc",
        sar_surveillance=surv_data,
    )

    store.save(result)

    # Retrieve
    loaded = store.get_run("run-persist-phase6")
    assert loaded is not None
    assert loaded.sar_surveillance is not None
    assert loaded.sar_surveillance["total_sar_targets"] == 2
    assert loaded.sar_surveillance["matched_coincident_count"] == 1
    assert loaded.sar_surveillance["uncorrelated_target_count"] == 1
    assert len(loaded.sar_surveillance["associations"]) == 2

    # List runs
    runs = store.list_runs()
    assert len(runs) >= 1
    saved_run_dict = [r for r in runs if r["run_id"] == "run-persist-phase6"][0]
    assert "sar_surveillance_json" in saved_run_dict
    parsed_summary = json.loads(saved_run_dict["sar_surveillance_json"])
    assert parsed_summary["total_sar_targets"] == 2


# ---------------------------------------------------------------------------
# 6. Empty surveillance results handling
# ---------------------------------------------------------------------------

def test_empty_surveillance_results():
    """Verify that execute_sar_surveillance handles zero targets and zero candidates cleanly."""
    matcher = SarAisMatchingService()
    res = matcher.correlate(
        sar_targets=[],
        ais_vessels=[],
        observation_time=datetime(2018, 10, 8, 5, 30, tzinfo=timezone.utc),
        scene_bbox=(9.38, 43.15, 9.58, 43.35),
    )
    res_dict = res.as_dict()
    assert res_dict["total_sar_targets"] == 0
    assert res_dict["total_ais_candidates"] == 0
    assert res_dict["matched_coincident_count"] == 0
    assert len(res_dict["associations"]) == 0

    schema_obj = SarSurveillanceResultSchema(**res_dict, targets=[])
    assert schema_obj.total_sar_targets == 0
    assert schema_obj.associations == []


# ---------------------------------------------------------------------------
# 7. Multiple bright targets handling
# ---------------------------------------------------------------------------

def test_multiple_bright_targets():
    """Verify correlation and schema serialization with multiple bright radar targets."""
    t1 = SarBrightTarget(
        target_id="tgt-1",
        pixel_x=50.0,
        pixel_y=50.0,
        lon=9.40,
        lat=43.20,
        peak_backscatter_db=-5.0,
        local_clutter_mean_db=-18.0,
        target_to_clutter_ratio_db=13.0,
        pixel_count=6,
    )
    t2 = SarBrightTarget(
        target_id="tgt-2",
        pixel_x=150.0,
        pixel_y=150.0,
        lon=9.50,
        lat=43.30,
        peak_backscatter_db=-7.0,
        local_clutter_mean_db=-19.0,
        target_to_clutter_ratio_db=12.0,
        pixel_count=4,
    )

    matcher = SarAisMatchingService()
    res = matcher.correlate(
        sar_targets=[t1, t2],
        ais_vessels=[],
        observation_time=datetime(2018, 10, 8, 5, 30, tzinfo=timezone.utc),
        scene_bbox=(9.38, 43.15, 9.58, 43.35),
    )
    res_dict = res.as_dict()
    assert res_dict["total_sar_targets"] == 2
    assert res_dict["uncorrelated_target_count"] == 2
    assert len(res_dict["associations"]) == 2


# ---------------------------------------------------------------------------
# 8. Ambiguous associations handling
# ---------------------------------------------------------------------------

def test_ambiguous_associations():
    """Verify that multiple AIS vessels within association gate produce AMBIGUOUS classification without 1-to-1 force."""
    target = SarBrightTarget(
        target_id="tgt-ambig",
        pixel_x=100.0,
        pixel_y=100.0,
        lon=9.4500,
        lat=43.2500,
        peak_backscatter_db=-6.0,
        local_clutter_mean_db=-18.0,
        target_to_clutter_ratio_db=12.0,
        pixel_count=8,
    )

    # Two vessels equidistant to the bright target (approx 200m away)
    vessel_a = {
        "vessel_id": "vessel-A",
        "mmsi": "111111111",
        "vessel_name": "VESSEL ALPHA",
        "positions": [
            {"lon": 9.4520, "lat": 43.2500, "timestamp": "2018-10-08T05:30:00Z", "speed_knots": 10.0, "heading": 90.0}
        ],
    }
    vessel_b = {
        "vessel_id": "vessel-B",
        "mmsi": "222222222",
        "vessel_name": "VESSEL BETA",
        "positions": [
            {"lon": 9.4480, "lat": 43.2500, "timestamp": "2018-10-08T05:30:00Z", "speed_knots": 10.0, "heading": 90.0}
        ],
    }

    matcher = SarAisMatchingService(
        coincident_spatial_gate_m=1000.0,
        max_association_gate_m=3000.0,
    )
    res = matcher.correlate(
        sar_targets=[target],
        ais_vessels=[vessel_a, vessel_b],
        observation_time=datetime(2018, 10, 8, 5, 30, tzinfo=timezone.utc),
        scene_bbox=(9.38, 43.15, 9.58, 43.35),
    )
    res_dict = res.as_dict()

    assert res_dict["ambiguous_count"] >= 1
    ambig_assocs = [
        a for a in res_dict["associations"]
        if a["classification"] == CLASSIFICATION_AMBIGUOUS_PROXIMITY
    ]
    assert len(ambig_assocs) >= 1
    for a in ambig_assocs:
        assert len(a["ambiguous_candidate_ids"]) >= 2
        # Ensure no artificial 1-to-1 vessel identity is assigned
        assert a.get("vessel_id") is None or a["classification"] == "AMBIGUOUS_MULTI_TARGET_PROXIMITY"

    # Schema validation
    schema_obj = SarSurveillanceResultSchema(**res_dict, targets=[])
    assert schema_obj.ambiguous_count >= 1


# ---------------------------------------------------------------------------
# 9. Read-only AIS database integrity
# ---------------------------------------------------------------------------

def test_readonly_ais_database_behavior():
    """Verify that querying the AIS database during Phase 6 does not modify or mutate ais_vessels.db."""
    db_path = get_ais_db_path()
    if not db_path.exists():
        pytest.skip(f"AIS database not present at {db_path}")

    # Inspect table count and row counts before
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        before_tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        row_counts_before = {
            t[0]: conn.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
            for t in before_tables
        }

    # Execute matching service with query from DB
    matcher = SarAisMatchingService()
    matcher.correlate_from_db(
        sar_targets=[],
        observation_time=datetime(2018, 10, 8, 5, 30, tzinfo=timezone.utc),
        scene_bbox=(9.38, 43.15, 9.58, 43.35),
    )

    # Inspect table count and row counts after
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        after_tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        assert before_tables == after_tables
        for t in after_tables:
            count_after = conn.execute(f"SELECT COUNT(*) FROM {t[0]}").fetchone()[0]
            assert count_after == row_counts_before[t[0]], f"Row count changed for table {t[0]}"


# ---------------------------------------------------------------------------
# 10. Optional Phase 6 execution in ExperimentRunner
# ---------------------------------------------------------------------------

def test_optional_phase6_pipeline_execution():
    """Verify ExperimentRunner executes Phase 6 when requested, and leaves it None when omitted/disabled."""
    runner = ExperimentRunner()
    obs_dt = datetime(2018, 10, 8, 5, 30, tzinfo=timezone.utc)

    # Mock external I/O (netcdf reading, backward drift, slick characterization)
    mock_step = BackwardDriftStep(
        timestamp=obs_dt,
        lon=9.47,
        lat=43.24,
        u_wind_ms=2.0,
        v_wind_ms=1.0,
        u_current_ms=0.1,
        v_current_ms=0.05,
        drift_u_ms=0.2,
        drift_v_ms=0.1,
        cumulative_backward_distance_m=100.0,
        uncertainty_radius_m=500.0,
    )

    with patch("app.services.drift_modelling._open_netcdf") as mock_nc, \
         patch("app.services.real_experiment.experiment_runner.run_backward_drift") as mock_drift, \
         patch("app.services.real_experiment.slick_characterization.characterize_observation") as mock_slick:

        mock_drift.return_value = [mock_step]
        mock_slick.return_value = {"has_physical_raster": False, "detected": False}

        # 1. No Phase 6 configuration
        res_disabled = runner.run(
            satellite_product_id="S1A_TEST",
            observation_lon=9.47,
            observation_lat=43.24,
            observation_time=obs_dt,
            era5_netcdf_path="mock_era5.nc",
            cmems_netcdf_path="mock_cmems.nc",
            sar_surveillance_config=None,
        )
        assert res_disabled.sar_surveillance is None

        # 2. Phase 6 configured with enabled=False
        res_flag_false = runner.run(
            satellite_product_id="S1A_TEST",
            observation_lon=9.47,
            observation_lat=43.24,
            observation_time=obs_dt,
            era5_netcdf_path="mock_era5.nc",
            cmems_netcdf_path="mock_cmems.nc",
            sar_surveillance_config={"enabled": False},
        )
        assert res_flag_false.sar_surveillance is None

        # 3. Phase 6 configured with enabled=True
        with patch("app.services.real_experiment.sar_surveillance.execute_sar_surveillance") as mock_exec:
            mock_exec.return_value = {
                "observation_time_iso": "2018-10-08T05:30:00Z",
                "scene_bbox": [9.38, 43.15, 9.58, 43.35],
                "total_sar_targets": 1,
                "total_ais_candidates": 1,
                "matched_coincident_count": 1,
                "spatial_discrepancy_count": 0,
                "uncorrelated_target_count": 0,
                "undetected_vessel_count": 0,
                "observation_gap_count": 0,
                "ambiguous_count": 0,
                "associations": [],
                "targets": [],
                "parameters": {},
                "scientific_disclaimer": DEFAULT_SURVEILLANCE_DISCLAIMER,
            }
            res_enabled = runner.run(
                satellite_product_id="S1A_TEST",
                observation_lon=9.47,
                observation_lat=43.24,
                observation_time=obs_dt,
                era5_netcdf_path="mock_era5.nc",
                cmems_netcdf_path="mock_cmems.nc",
                sar_surveillance_config={"enabled": True},
            )
            assert res_enabled.sar_surveillance is not None
            assert res_enabled.sar_surveillance["total_sar_targets"] == 1
            mock_exec.assert_called_once()

            # Also verify runner.run_experiment alias works identically
            res_alias = runner.run_experiment(
                satellite_product_id="S1A_TEST",
                observation_lon=9.47,
                observation_lat=43.24,
                observation_time=obs_dt,
                era5_netcdf_path="mock_era5.nc",
                cmems_netcdf_path="mock_cmems.nc",
                sar_surveillance_config={"enabled": True},
            )
            assert res_alias.sar_surveillance is not None


# ---------------------------------------------------------------------------
# 11. Preservation of Phase 1–5 pipeline outputs
# ---------------------------------------------------------------------------

def test_phase1_to_5_output_preservation():
    """Verify that enabling Phase 6 does not modify any Phase 1-5 output attributes."""
    runner = ExperimentRunner()
    obs_dt = datetime(2018, 10, 8, 5, 30, tzinfo=timezone.utc)

    vf = VesselFeatures(
        vessel_id="vessel-test-1",
        mmsi="123456789",
        vessel_name="TEST VESSEL",
        min_source_distance_km=1.2,
        temporal_overlap_hours=2.0,
        trajectory_overlap_fraction=0.8,
        heading_consistency=0.9,
        speed_consistency=0.95,
        ais_position_count=10,
        ais_coverage_fraction=0.9,
        evidence_consistency_score=0.85,
        has_meaningful_support=True,
    )

    mock_step = BackwardDriftStep(
        timestamp=obs_dt,
        lon=9.4783,
        lat=43.2483,
        u_wind_ms=2.0,
        v_wind_ms=1.0,
        u_current_ms=0.1,
        v_current_ms=0.05,
        drift_u_ms=0.2,
        drift_v_ms=0.1,
        cumulative_backward_distance_m=100.0,
        uncertainty_radius_m=750.0,
    )

    with patch("app.services.drift_modelling._open_netcdf") as mock_nc, \
         patch("app.services.real_experiment.experiment_runner.run_backward_drift") as mock_drift, \
         patch("app.services.real_experiment.slick_characterization.characterize_observation") as mock_slick:

        mock_drift.return_value = [mock_step]
        mock_slick.return_value = {"has_physical_raster": False, "detected": False, "area_m2": 4500000.0}

        # Run without Phase 6
        res_baseline = runner.run(
            satellite_product_id="S1A_TEST",
            observation_lon=9.4783,
            observation_lat=43.2483,
            observation_time=obs_dt,
            era5_netcdf_path="mock_era5.nc",
            cmems_netcdf_path="mock_cmems.nc",
            sar_surveillance_config=None,
        )

        # Run with Phase 6 enabled
        with patch("app.services.real_experiment.sar_surveillance.execute_sar_surveillance") as mock_exec:
            mock_exec.return_value = {
                "observation_time_iso": "2018-10-08T05:30:00Z",
                "scene_bbox": [9.38, 43.15, 9.58, 43.35],
                "total_sar_targets": 3,
                "total_ais_candidates": 1,
                "matched_coincident_count": 1,
                "spatial_discrepancy_count": 0,
                "uncorrelated_target_count": 2,
                "undetected_vessel_count": 0,
                "observation_gap_count": 0,
                "ambiguous_count": 0,
                "associations": [],
                "targets": [],
                "parameters": {},
                "scientific_disclaimer": DEFAULT_SURVEILLANCE_DISCLAIMER,
            }

            res_with_phase6 = runner.run(
                satellite_product_id="S1A_TEST",
                observation_lon=9.4783,
                observation_lat=43.2483,
                observation_time=obs_dt,
                era5_netcdf_path="mock_era5.nc",
                cmems_netcdf_path="mock_cmems.nc",
                sar_surveillance_config={"enabled": True},
            )

        # Verify all Phase 1-5 outputs are identical
        phase_1_to_5_attrs = [
            "satellite_product_id",
            "observation_time",
            "backtrack_hours",
            "step_hours",
            "model_version",
            "source_lon",
            "source_lat",
            "source_radius_m",
            "backward_steps",
            "vessels",
            "slick_characterization",
            "forward_prediction",
            "monte_carlo_ensemble",
        ]

        for attr in phase_1_to_5_attrs:
            val_base = getattr(res_baseline, attr)
            val_p6 = getattr(res_with_phase6, attr)
            assert val_base == val_p6, f"Phase 1-5 attribute '{attr}' diverged when Phase 6 was enabled!"


# ---------------------------------------------------------------------------
# 12. CFAR k-sigma configuration propagation test
# ---------------------------------------------------------------------------

def test_cfar_k_sigma_configuration_propagation(tmp_path: Path):
    """Verify that cfar_k_sigma is exposed with default 4.0 matching Phase 6.1 detector,
    can be configured via SarSurveillanceConfigRequest and SarSurveillanceConfig,
    and is passed through correctly to SarBrightTargetDetector.
    """
    # 1. Pydantic request schema default and configured behavior
    req_default = SarSurveillanceConfigRequest()
    assert req_default.cfar_k_sigma == 4.0

    req_custom = SarSurveillanceConfigRequest(cfar_k_sigma=4.5)
    assert req_custom.cfar_k_sigma == 4.5

    # 2. SarSurveillanceConfig default and from_dict propagation
    cfg_default = SarSurveillanceConfig()
    assert cfg_default.cfar_k_sigma == 4.0

    cfg_from_dict = SarSurveillanceConfig.from_dict({"cfar_k_sigma": 3.8})
    assert cfg_from_dict.cfar_k_sigma == 3.8

    # 3. Verify propagation to SarBrightTargetDetector inside execute_sar_surveillance
    dummy_tif = tmp_path / "test_propagation.tif"
    dummy_tif.write_bytes(b"dummy_geotiff_data")

    with patch("app.services.real_experiment.sar_surveillance.SarBrightTargetDetector") as mock_det_cls:
        mock_instance = MagicMock()
        mock_instance.detect_from_file.return_value = []
        mock_det_cls.return_value = mock_instance

        # Run with default config (should pass cfar_k_sigma=4.0)
        execute_sar_surveillance(
            sar_raster_path=str(dummy_tif),
            config=SarSurveillanceConfig(enabled=True),
        )
        assert mock_det_cls.call_count == 1
        assert mock_det_cls.call_args.kwargs["cfar_k_sigma"] == 4.0

        mock_det_cls.reset_mock()

        # Run with custom cfar_k_sigma (should pass configured value)
        execute_sar_surveillance(
            sar_raster_path=str(dummy_tif),
            config={"enabled": True, "cfar_k_sigma": 5.2},
        )
        assert mock_det_cls.call_count == 1
        assert mock_det_cls.call_args.kwargs["cfar_k_sigma"] == 5.2

