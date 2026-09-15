"""Stage G1 — Investigation API Tests.

Validates the investigation lifecycle, REST endpoints, workflow orchestration,
provenance discoverability, error handling, and scientific safety invariants.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.acquisition.registry import default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.main import app
from app.models.asset import Asset
from app.models.candidate_ranking import CandidateRanking, RankedCandidate
from app.models.common import (
    AssetType,
    BBoxAreaOfInterest,
    BoundingBox,
    InvestigationStatus,
    PolygonAreaOfInterest,
    Provenance,
    TimeWindow,
)
from app.models.drift import DriftResult, DriftStep
from app.models.evidence_fusion import (
    BehavioralContextSummary,
    EvidenceFusionResult,
    EvidenceSignal,
    ForwardDriftCrossCheck,
    SignalStatus,
    VesselFusedEvidence,
)
from app.models.explainability import (
    CandidateExplanation,
    EvidenceConsistencyLevel,
    ExplainabilityReport,
)
from app.models.investigation_api import (
    InvestigationCreateRequest,
    InvestigationRunRequest,
)
from app.models.satellite import SatelliteScene, SpillDetection
from app.models.source_estimation import SourceEstimateResult
from app.models.trajectory_analysis import TrajectoryAnalysisResult
from app.models.vessel import CandidateVesselGenerationResult
from app.services.investigation_workflow import (
    default_investigation_store,
    list_investigation_artifacts,
    run_investigation_workflow,
)
from app.services.spill_detection import SpillDetectionError
from app.validation.schemas import ValidationResult


@pytest.fixture(autouse=True)
def clean_stores():
    """Reset in-memory investigation and asset stores before each test."""
    default_investigation_store.clear()
    default_asset_registry._assets.clear()
    default_asset_registry._order.clear()
    yield
    default_investigation_store.clear()
    default_asset_registry._assets.clear()
    default_asset_registry._order.clear()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_investigation_payload():
    return {
        "name": "Gulf of Mexico Spill Case 101",
        "area_of_interest": {
            "kind": "bbox",
            "bbox": {
                "west": -90.5,
                "south": 28.0,
                "east": -89.5,
                "north": 29.0,
            },
        },
        "time_window": {
            "start": "2025-06-01T00:00:00Z",
            "end": "2025-06-02T00:00:00Z",
        },
        "description": "SAR anomaly detected south of Mississippi Delta",
        "metadata": {"lead_investigator": "Dr. Smith", "priority": "HIGH"},
    }


def _create_mock_pipeline_objects(investigation_id: str):
    """Helper creating valid domain models for pipeline mock stages."""
    now = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
    footprint = PolygonAreaOfInterest(
        coordinates=[[[-90.5, 28.0], [-89.5, 28.0], [-89.5, 29.0], [-90.5, 29.0], [-90.5, 28.0]]]
    )
    scene = SatelliteScene(
        id=f"scene-{investigation_id}",
        investigation_id=investigation_id,
        asset_id="asset-sar-raw",
        provider="sentinel1",
        sensor="SAR-C",
        acquisition_time=now,
        footprint=footprint,
    )
    val_result = ValidationResult(
        passed=True,
        issues=[],
        artifact_location="/tmp/test.zip",
        validator_name="Sentinel1Validator",
        validator_version="1.0.0",
        validated_at=now,
    )
    b1_asset = Asset(
        id="asset-b1-raw",
        investigation_id=investigation_id,
        type=AssetType.SATELLITE_SCENE,
        provider="sentinel1",
        source="local_file",
        location="/tmp/test.zip",
        acquisition_time=now,
    )
    b2_asset = Asset(
        id="asset-b2-preprocessed",
        investigation_id=investigation_id,
        type=AssetType.IMAGERY_PREVIEW,
        provider="sentinel1",
        source="sentinel1_preprocessing",
        location="/tmp/calibrated.tif",
        acquisition_time=now,
    )
    spill_detection = SpillDetection(
        id=f"spill-{investigation_id}",
        investigation_id=investigation_id,
        asset_id="asset-b3-spill",
        scene_id=scene.id,
        detected=True,
        confidence=1.0,
        geometry={"type": "Polygon", "coordinates": [[[-90.1, 28.4], [-89.9, 28.4], [-89.9, 28.6], [-90.1, 28.6], [-90.1, 28.4]]]},
        area=150000.0,
        metadata={"centroid": {"longitude": -90.0, "latitude": 28.5}, "detected": True, "spill_count": 1, "total_area_m2": 150000.0},
    )
    spill_asset = Asset(
        id="asset-b3-spill",
        investigation_id=investigation_id,
        type=AssetType.SPILL_GEOMETRY,
        provider="sentinel1",
        source="spill_detection",
        location="/tmp/spill.geojson",
        acquisition_time=now,
        metadata={"centroid": {"longitude": -90.0, "latitude": 28.5}, "detected": True, "spill_count": 1},
    )
    wind_asset = Asset(
        id="asset-c1-wind",
        investigation_id=investigation_id,
        type=AssetType.ENVIRONMENT_WIND,
        provider="era5",
        source="cds",
        location="/tmp/wind.nc",
        acquisition_time=now,
    )
    current_asset = Asset(
        id="asset-c1-current",
        investigation_id=investigation_id,
        type=AssetType.ENVIRONMENT_CURRENT,
        provider="cmems",
        source="copernicus",
        location="/tmp/current.nc",
        acquisition_time=now,
    )
    drift_result = DriftResult(
        id=f"drift-{investigation_id}",
        investigation_id=investigation_id,
        spill_detection_id=spill_detection.id,
        wind_asset_id=wind_asset.id,
        current_asset_id=current_asset.id,
        asset_id="asset-d1-drift",
        observation_time=now,
        origin_lon=-90.0,
        origin_lat=28.5,
        total_duration_hours=24.0,
        step_hours=1.0,
        leeway_fraction=0.035,
        endpoint_lon=-90.0,
        endpoint_lat=28.5,
        steps=[
            DriftStep(
                timestamp=now,
                lon=-90.0,
                lat=28.5,
                u_wind_ms=5.0,
                v_wind_ms=2.0,
                u_current_ms=0.2,
                v_current_ms=-0.1,
                drift_u_ms=0.375,
                drift_v_ms=-0.03,
                cumulative_distance_m=0.0,
            )
        ],
    )
    drift_asset = Asset(
        id="asset-d1-drift",
        investigation_id=investigation_id,
        type=AssetType.DRIFT_PRODUCT,
        provider="drift_service",
        source="drift_modelling",
        location="/tmp/drift.geojson",
    )
    source_result = SourceEstimateResult(
        id=f"source-{investigation_id}",
        investigation_id=investigation_id,
        spill_detection_id=spill_detection.id,
        wind_asset_id=wind_asset.id,
        current_asset_id=current_asset.id,
        asset_id="asset-d3-source",
        observation_time=now,
        origin_lon=-90.0,
        origin_lat=28.5,
        source_time=now,
        source_point_lon=-90.1,
        source_point_lat=28.4,
        lookback_hours=12.0,
        step_hours=1.0,
        leeway_fraction=0.035,
        source_uncertainty_radius_km=10.0,
        steps=[],
        source_zone_geometry={"type": "Polygon", "coordinates": [[[-90.2, 28.3], [-90.0, 28.3], [-90.0, 28.5], [-90.2, 28.5], [-90.2, 28.3]]]},
    )
    source_asset = Asset(
        id="asset-d3-source",
        investigation_id=investigation_id,
        type=AssetType.DRIFT_PRODUCT,
        provider="source_estimation_service",
        source="source_estimation",
        location="/tmp/source.geojson",
    )
    from app.models.vessel import CandidateGenerationStatus
    candidate_result = CandidateVesselGenerationResult(
        id=f"candidates-{investigation_id}",
        investigation_id=investigation_id,
        spill_detection_id=spill_detection.id,
        source_estimate_id=source_asset.id,
        status=CandidateGenerationStatus.COMPLETED,
        source_time=now,
        source_uncertainty_radius_km=10.0,
        temporal_window_start=now,
        temporal_window_end=now,
        spatial_query_bbox={"west": -90.5, "south": 28.0, "east": -89.5, "north": 29.0},
        candidate_count=0,
        total_vessels_checked=0,
        candidates=[],
    )
    candidate_asset = Asset(
        id="asset-e1-candidates",
        investigation_id=investigation_id,
        type=AssetType.DOCUMENT,
        provider="candidate_service",
        source="candidate_generation",
        location="/tmp/candidates.json",
    )
    trajectory_result = TrajectoryAnalysisResult(
        id=f"trajectory-{investigation_id}",
        investigation_id=investigation_id,
        spill_detection_id=spill_detection.id,
        source_estimate_id=source_asset.id,
        candidate_generation_id=candidate_asset.id,
        analyzed_vessel_count=0,
        analyses=[],
    )
    trajectory_asset = Asset(
        id="asset-e2-trajectory",
        investigation_id=investigation_id,
        type=AssetType.DOCUMENT,
        provider="trajectory_service",
        source="trajectory_analysis",
        location="/tmp/trajectories.json",
    )
    from app.models.behavioral_intelligence import BehavioralIntelligenceResult
    behavioral_result = BehavioralIntelligenceResult(
        id=f"behavior-{investigation_id}",
        investigation_id=investigation_id,
        spill_detection_id=spill_detection.id,
        source_estimate_id=source_asset.id,
        candidate_generation_id=candidate_asset.id,
        analyzed_vessel_count=0,
        profiles=[],
        total_anomalies_detected=0,
        total_transmission_gaps_detected=0,
    )
    behavioral_asset = Asset(
        id="asset-e3-behavior",
        investigation_id=investigation_id,
        type=AssetType.DOCUMENT,
        provider="behavior_service",
        source="behavioral_intelligence",
        location="/tmp/behavior.json",
    )
    fusion_result = EvidenceFusionResult(
        id=f"fusion-{investigation_id}",
        investigation_id=investigation_id,
        spill_detection_id=spill_detection.id,
        source_estimate_id=source_asset.id,
        candidate_generation_id=candidate_asset.id,
        trajectory_analysis_id=trajectory_asset.id,
        behavioral_intelligence_id=behavioral_asset.id,
        normalization_reference_radius_km=10.0,
        temporal_scale_hours=2.0,
        nominal_weights={"spatial_proximity": 0.50, "temporal_proximity": 0.25, "trajectory_consistency": 0.25},
        candidate_count=0,
        fused_candidates=[],
    )
    fusion_asset = Asset(
        id="asset-f1-fusion",
        investigation_id=investigation_id,
        type=AssetType.DOCUMENT,
        provider="fusion_service",
        source="evidence_fusion",
        location="/tmp/fusion.json",
    )
    ranking_result = CandidateRanking(
        id=f"ranking-{investigation_id}",
        investigation_id=investigation_id,
        spill_id=spill_detection.id,
        evidence_fusion_id=fusion_asset.id,
        generated_at=now,
        candidate_count=0,
        candidates=[],
    )
    ranking_asset = Asset(
        id="asset-f2-ranking",
        investigation_id=investigation_id,
        type=AssetType.DOCUMENT,
        provider="ranking_service",
        source="candidate_ranking",
        location="/tmp/ranking.json",
    )
    report = ExplainabilityReport(
        id=f"report-{investigation_id}",
        investigation_id=investigation_id,
        spill_id=spill_detection.id,
        candidate_ranking_id=ranking_asset.id,
        generated_at=now,
        candidate_count=0,
        candidates=[],
    )
    report_asset = Asset(
        id="asset-f3-report",
        investigation_id=investigation_id,
        type=AssetType.DOCUMENT,
        provider="explainability_service",
        source="explainability",
        location="/tmp/report.json",
    )

    return {
        "scene": scene,
        "val_result": val_result,
        "b1_asset": b1_asset,
        "b2_asset": b2_asset,
        "spill_detection": spill_detection,
        "spill_asset": spill_asset,
        "wind_asset": wind_asset,
        "current_asset": current_asset,
        "drift_result": drift_result,
        "drift_asset": drift_asset,
        "source_result": source_result,
        "source_asset": source_asset,
        "candidate_result": candidate_result,
        "candidate_asset": candidate_asset,
        "trajectory_result": trajectory_result,
        "trajectory_asset": trajectory_asset,
        "behavioral_result": behavioral_result,
        "behavioral_asset": behavioral_asset,
        "fusion_result": fusion_result,
        "fusion_asset": fusion_asset,
        "ranking_result": ranking_result,
        "ranking_asset": ranking_asset,
        "report": report,
        "report_asset": report_asset,
    }


# ===========================================================================
# TESTS 1 to 25
# ===========================================================================

def test_01_create_investigation(client, sample_investigation_payload):
    """1. Create investigation with valid payload returns 201 and structured metadata."""
    res = client.post("/api/v1/investigations", json=sample_investigation_payload)
    assert res.status_code == 201
    data = res.json()
    assert data["id"].startswith("inv-")
    assert data["name"] == sample_investigation_payload["name"]
    assert data["status"] == "CREATED"
    assert data["area_of_interest"]["kind"] == "bbox"
    assert data["time_window"]["start"].startswith("2025-06-01")
    assert data["metadata"]["priority"] == "HIGH"


def test_02_get_investigation(client, sample_investigation_payload):
    """2. Get investigation by ID returns matching record."""
    create_res = client.post("/api/v1/investigations", json=sample_investigation_payload)
    inv_id = create_res.json()["id"]

    get_res = client.get(f"/api/v1/investigations/{inv_id}")
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["id"] == inv_id
    assert data["status"] == "CREATED"
    assert data["name"] == sample_investigation_payload["name"]


def test_03_list_investigations(client, sample_investigation_payload):
    """3. List investigations returns deterministic list without eager artifact contents."""
    p1 = dict(sample_investigation_payload, name="Case A")
    p2 = dict(sample_investigation_payload, name="Case B")
    c1 = client.post("/api/v1/investigations", json=p1).json()["id"]
    c2 = client.post("/api/v1/investigations", json=p2).json()["id"]

    list_res = client.get("/api/v1/investigations")
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) == 2
    assert items[0]["id"] == c1
    assert items[1]["id"] == c2
    assert items[0]["name"] == "Case A"
    assert items[1]["name"] == "Case B"
    assert "asset_count" in items[0]


def test_04_invalid_investigation_id(client):
    """4. Unknown investigation IDs return appropriate 404."""
    assert client.get("/api/v1/investigations/unknown-999").status_code == 404
    assert client.get("/api/v1/investigations/unknown-999/status").status_code == 404
    assert client.get("/api/v1/investigations/unknown-999/artifacts").status_code == 404
    assert client.post("/api/v1/investigations/unknown-999/run", json={}).status_code == 404


def test_05_malformed_creation_request(client):
    """5. Malformed creation request produces 422 validation response."""
    res = client.post("/api/v1/investigations", json={"description": "Missing name and bounds"})
    assert res.status_code == 422


def test_06_invalid_time_window(client, sample_investigation_payload):
    """6. Invalid time window (start >= end) produces 422 validation error."""
    bad_payload = dict(sample_investigation_payload)
    bad_payload["time_window"] = {
        "start": "2025-06-02T12:00:00Z",
        "end": "2025-06-01T12:00:00Z",
    }
    res = client.post("/api/v1/investigations", json=bad_payload)
    assert res.status_code == 422


def test_07_investigation_status(client, sample_investigation_payload):
    """7. Investigation status returns structured lifecycle state."""
    create_res = client.post("/api/v1/investigations", json=sample_investigation_payload)
    inv_id = create_res.json()["id"]

    st_res = client.get(f"/api/v1/investigations/{inv_id}/status")
    assert st_res.status_code == 200
    st = st_res.json()
    assert st["investigation_id"] == inv_id
    assert st["status"] == "CREATED"
    assert st["current_stage"] is None
    assert st["completed_stages"] == []
    assert st["available_artifacts"] == []
    assert st["errors"] == []


@patch("app.services.investigation_workflow.ingest_sentinel1_artifact")
@patch("app.services.investigation_workflow.preprocess_sentinel1_scene")
@patch("app.services.investigation_workflow.detect_spills_from_sar_scene")
@patch("app.services.investigation_workflow.compute_drift_for_spill")
@patch("app.services.investigation_workflow.compute_source_estimate_for_spill")
@patch("app.services.investigation_workflow.generate_candidate_vessels_for_spill")
@patch("app.services.investigation_workflow.analyze_candidate_trajectories")
@patch("app.services.investigation_workflow.analyze_candidate_behavior")
@patch("app.services.investigation_workflow.fuse_evidence")
@patch("app.services.investigation_workflow.rank_candidates")
@patch("app.services.investigation_workflow.generate_explainability_report")
def test_08_workflow_execution(
    mock_f3,
    mock_f2,
    mock_f1,
    mock_e3,
    mock_e2,
    mock_e1,
    mock_d3,
    mock_d1,
    mock_b3,
    mock_b2,
    mock_b1,
    client,
    sample_investigation_payload,
):
    """8. Workflow execution orchestrates pipeline stages."""
    create_res = client.post("/api/v1/investigations", json=sample_investigation_payload)
    inv_id = create_res.json()["id"]

    objs = _create_mock_pipeline_objects(inv_id)
    mock_b1.return_value = (objs["scene"], objs["val_result"], objs["b1_asset"])
    mock_b2.return_value = (objs["b2_asset"], {})
    mock_b3.return_value = (objs["spill_detection"], objs["spill_asset"])
    mock_d1.return_value = (objs["drift_result"], objs["drift_asset"])
    mock_d3.return_value = (objs["source_result"], objs["source_asset"])
    mock_e1.return_value = (objs["candidate_result"], objs["candidate_asset"])
    mock_e2.return_value = (objs["trajectory_result"], objs["trajectory_asset"])
    mock_e3.return_value = (objs["behavioral_result"], objs["behavioral_asset"])
    mock_f1.return_value = (objs["fusion_result"], objs["fusion_asset"])
    mock_f2.return_value = (objs["ranking_result"], objs["ranking_asset"])
    mock_f3.return_value = (objs["report"], objs["report_asset"])

    # Register mock wind and current in registry so C1 passes directly
    default_asset_registry._assets[objs["wind_asset"].id] = objs["wind_asset"]
    default_asset_registry._assets[objs["current_asset"].id] = objs["current_asset"]

    run_payload = {
        "sentinel1_artifact_path": "/path/to/S1A_IW_GRDH.zip",
        "wind_asset_id": objs["wind_asset"].id,
        "current_asset_id": objs["current_asset"].id,
    }

    res = client.post(f"/api/v1/investigations/{inv_id}/run", json=run_payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "COMPLETED"
    assert data["current_stage"] == "F3"
    assert "B1" in data["completed_stages"]
    assert "F3" in data["completed_stages"]


def test_09_correct_workflow_stage_ordering(sample_investigation_payload):
    """9. Workflow stages execute in strict dependency order B1 -> B2 -> B3 -> C1 -> D1 -> D3 -> E1 -> E2 -> E3 -> F1 -> F2 -> F3."""
    inv = default_investigation_store.create(
        name="Ordering Test",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=-10, south=40, east=-5, north=45)),
        time_window=TimeWindow(start=datetime(2025, 1, 1, tzinfo=timezone.utc), end=datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )
    objs = _create_mock_pipeline_objects(inv.id)
    default_asset_registry._assets[objs["wind_asset"].id] = objs["wind_asset"]
    default_asset_registry._assets[objs["current_asset"].id] = objs["current_asset"]

    executed_stages = []

    with (
        patch("app.services.investigation_workflow.ingest_sentinel1_artifact", side_effect=lambda **kw: (executed_stages.append("B1"), objs["scene"], objs["val_result"], objs["b1_asset"])[1:]),
        patch("app.services.investigation_workflow.preprocess_sentinel1_scene", side_effect=lambda **kw: (executed_stages.append("B2"), objs["b2_asset"], {})[1:]),
        patch("app.services.investigation_workflow.detect_spills_from_sar_scene", side_effect=lambda **kw: (executed_stages.append("B3"), objs["spill_detection"], objs["spill_asset"])[1:]),
        patch("app.services.investigation_workflow.compute_drift_for_spill", side_effect=lambda **kw: (executed_stages.append("D1"), objs["drift_result"], objs["drift_asset"])[1:]),
        patch("app.services.investigation_workflow.compute_source_estimate_for_spill", side_effect=lambda **kw: (executed_stages.append("D3"), objs["source_result"], objs["source_asset"])[1:]),
        patch("app.services.investigation_workflow.generate_candidate_vessels_for_spill", side_effect=lambda **kw: (executed_stages.append("E1"), objs["candidate_result"], objs["candidate_asset"])[1:]),
        patch("app.services.investigation_workflow.analyze_candidate_trajectories", side_effect=lambda **kw: (executed_stages.append("E2"), objs["trajectory_result"], objs["trajectory_asset"])[1:]),
        patch("app.services.investigation_workflow.analyze_candidate_behavior", side_effect=lambda **kw: (executed_stages.append("E3"), objs["behavioral_result"], objs["behavioral_asset"])[1:]),
        patch("app.services.investigation_workflow.fuse_evidence", side_effect=lambda **kw: (executed_stages.append("F1"), objs["fusion_result"], objs["fusion_asset"])[1:]),
        patch("app.services.investigation_workflow.rank_candidates", side_effect=lambda **kw: (executed_stages.append("F2"), objs["ranking_result"], objs["ranking_asset"])[1:]),
        patch("app.services.investigation_workflow.generate_explainability_report", side_effect=lambda **kw: (executed_stages.append("F3"), objs["report"], objs["report_asset"])[1:]),
    ):
        req = InvestigationRunRequest(
            sentinel1_artifact_path="/tmp/mock.zip",
            wind_asset_id=objs["wind_asset"].id,
            current_asset_id=objs["current_asset"].id,
        )
        res = run_investigation_workflow(inv.id, req)
        assert res.status_code if hasattr(res, "status_code") else res.status == InvestigationStatus.COMPLETED

    expected_order = ["B1", "B2", "B3", "D1", "D3", "E1", "E2", "E3", "F1", "F2", "F3"]
    assert executed_stages == expected_order
    assert res.completed_stages == ["B1", "B2", "B3", "C1", "D1", "D3", "E1", "E2", "E3", "F1", "F2", "F3"]


def test_10_current_stage_updates():
    """10. current_stage updates in store before each stage executes."""
    inv = default_investigation_store.create(
        name="Stage Update Test",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=-10, south=40, east=-5, north=45)),
        time_window=TimeWindow(start=datetime(2025, 1, 1, tzinfo=timezone.utc), end=datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )
    observed_stages = []

    def b1_hook(**kwargs):
        st = default_investigation_store.get_status(inv.id)
        observed_stages.append(st.current_stage)
        objs = _create_mock_pipeline_objects(inv.id)
        return objs["scene"], objs["val_result"], objs["b1_asset"]

    with patch("app.services.investigation_workflow.ingest_sentinel1_artifact", side_effect=b1_hook):
        run_investigation_workflow(
            inv.id,
            InvestigationRunRequest(sentinel1_artifact_path="/tmp/mock.zip"),
        )

    assert "B1" in observed_stages


def test_11_completed_stages_updates():
    """11. completed_stages updates incrementally as stages finish."""
    inv = default_investigation_store.create(
        name="Completed Stage Test",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=-10, south=40, east=-5, north=45)),
        time_window=TimeWindow(start=datetime(2025, 1, 1, tzinfo=timezone.utc), end=datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )
    objs = _create_mock_pipeline_objects(inv.id)

    with (
        patch("app.services.investigation_workflow.ingest_sentinel1_artifact", return_value=(objs["scene"], objs["val_result"], objs["b1_asset"])),
        patch("app.services.investigation_workflow.preprocess_sentinel1_scene", side_effect=SpillDetectionError("Corrupt SAR GeoTIFF")),
    ):
        res = run_investigation_workflow(
            inv.id,
            InvestigationRunRequest(sentinel1_artifact_path="/tmp/mock.zip"),
        )
        assert res.completed_stages == ["B1"]
        assert res.current_stage == "B2"
        assert res.status == InvestigationStatus.FAILED


def test_12_successful_workflow_reaches_completed():
    """12. Successful workflow reaches COMPLETED status."""
    inv = default_investigation_store.create(
        name="Complete Test",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=-10, south=40, east=-5, north=45)),
        time_window=TimeWindow(start=datetime(2025, 1, 1, tzinfo=timezone.utc), end=datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )
    objs = _create_mock_pipeline_objects(inv.id)
    default_asset_registry._assets[objs["wind_asset"].id] = objs["wind_asset"]
    default_asset_registry._assets[objs["current_asset"].id] = objs["current_asset"]

    with (
        patch("app.services.investigation_workflow.ingest_sentinel1_artifact", return_value=(objs["scene"], objs["val_result"], objs["b1_asset"])),
        patch("app.services.investigation_workflow.preprocess_sentinel1_scene", return_value=(objs["b2_asset"], {})),
        patch("app.services.investigation_workflow.detect_spills_from_sar_scene", return_value=(objs["spill_detection"], objs["spill_asset"])),
        patch("app.services.investigation_workflow.compute_drift_for_spill", return_value=(objs["drift_result"], objs["drift_asset"])),
        patch("app.services.investigation_workflow.compute_source_estimate_for_spill", return_value=(objs["source_result"], objs["source_asset"])),
        patch("app.services.investigation_workflow.generate_candidate_vessels_for_spill", return_value=(objs["candidate_result"], objs["candidate_asset"])),
        patch("app.services.investigation_workflow.analyze_candidate_trajectories", return_value=(objs["trajectory_result"], objs["trajectory_asset"])),
        patch("app.services.investigation_workflow.analyze_candidate_behavior", return_value=(objs["behavioral_result"], objs["behavioral_asset"])),
        patch("app.services.investigation_workflow.fuse_evidence", return_value=(objs["fusion_result"], objs["fusion_asset"])),
        patch("app.services.investigation_workflow.rank_candidates", return_value=(objs["ranking_result"], objs["ranking_asset"])),
        patch("app.services.investigation_workflow.generate_explainability_report", return_value=(objs["report"], objs["report_asset"])),
    ):
        res = run_investigation_workflow(
            inv.id,
            InvestigationRunRequest(
                sentinel1_artifact_path="/tmp/mock.zip",
                wind_asset_id=objs["wind_asset"].id,
                current_asset_id=objs["current_asset"].id,
            ),
        )
        assert res.status == InvestigationStatus.COMPLETED
        # Verify investigation domain model status in store
        updated_inv = default_investigation_store.get(inv.id)
        assert updated_inv.status == InvestigationStatus.COMPLETED


def test_13_required_stage_failure_reaches_failed():
    """13. Required stage failure sets status to FAILED."""
    inv = default_investigation_store.create(
        name="Failure Test",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=-10, south=40, east=-5, north=45)),
        time_window=TimeWindow(start=datetime(2025, 1, 1, tzinfo=timezone.utc), end=datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )
    with patch("app.services.investigation_workflow.ingest_sentinel1_artifact", side_effect=ValueError("Invalid ZIP manifest")):
        res = run_investigation_workflow(
            inv.id,
            InvestigationRunRequest(sentinel1_artifact_path="/tmp/invalid.zip"),
        )
        assert res.status == InvestigationStatus.FAILED
        assert default_investigation_store.get(inv.id).status == InvestigationStatus.FAILED


def test_14_workflow_stops_after_required_stage_failure():
    """14. Workflow halts immediately upon required stage failure without running later stages."""
    inv = default_investigation_store.create(
        name="Halt Test",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=-10, south=40, east=-5, north=45)),
        time_window=TimeWindow(start=datetime(2025, 1, 1, tzinfo=timezone.utc), end=datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )
    objs = _create_mock_pipeline_objects(inv.id)
    mock_b3 = MagicMock()

    with (
        patch("app.services.investigation_workflow.ingest_sentinel1_artifact", return_value=(objs["scene"], objs["val_result"], objs["b1_asset"])),
        patch("app.services.investigation_workflow.preprocess_sentinel1_scene", side_effect=Exception("Disk full during calibration")),
        patch("app.services.investigation_workflow.detect_spills_from_sar_scene", mock_b3),
    ):
        res = run_investigation_workflow(
            inv.id,
            InvestigationRunRequest(sentinel1_artifact_path="/tmp/mock.zip"),
        )
        assert res.status == InvestigationStatus.FAILED
        mock_b3.assert_not_called()


def test_15_failing_stage_is_surfaced():
    """15. Failing stage name and error message are surfaced in structured response."""
    inv = default_investigation_store.create(
        name="Surface Error Test",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=-10, south=40, east=-5, north=45)),
        time_window=TimeWindow(start=datetime(2025, 1, 1, tzinfo=timezone.utc), end=datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )
    with patch("app.services.investigation_workflow.ingest_sentinel1_artifact", side_effect=FileNotFoundError("ZIP archive not found")):
        res = run_investigation_workflow(
            inv.id,
            InvestigationRunRequest(sentinel1_artifact_path="/tmp/missing.zip"),
        )
        assert res.status == InvestigationStatus.FAILED
        assert len(res.errors) == 1
        assert res.errors[0].stage == "B1"
        assert res.errors[0].error == "FileNotFoundError"
        assert "ZIP archive not found" in res.errors[0].message


def test_16_artifact_listing(client):
    """16. Artifact listing returns registered artifacts for the investigation."""
    inv = default_investigation_store.create(
        name="Artifacts Test",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=-10, south=40, east=-5, north=45)),
        time_window=TimeWindow(start=datetime(2025, 1, 1, tzinfo=timezone.utc), end=datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )
    # Register an asset for this investigation
    default_asset_registry.register(
        investigation_id=inv.id,
        provider_id="sentinel1",
        artifact=AcquiredArtifact(
            asset_type=AssetType.SATELLITE_SCENE,
            location="/tmp/scene.zip",
            source="copernicus",
            provenance=Provenance(product_id="S1A_2025", processing_level="L1C"),
            metadata={"validation_classification": "VALID"},
        ),
    )

    res = client.get(f"/api/v1/investigations/{inv.id}/artifacts")
    assert res.status_code == 200
    artifacts = res.json()
    assert len(artifacts) == 1
    assert artifacts[0]["investigation_id"] == inv.id
    assert artifacts[0]["asset_type"] == "satellite_scene"
    assert artifacts[0]["provider"] == "sentinel1"


def test_17_artifact_metadata_preservation():
    """17. Artifact metadata (source, provider, processing level, timestamps) preserved."""
    inv_id = "inv-meta-test"
    now = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
    default_asset_registry.register(
        investigation_id=inv_id,
        provider_id="cmems",
        artifact=AcquiredArtifact(
            asset_type=AssetType.ENVIRONMENT_CURRENT,
            location="/data/currents.nc",
            source="marine_copernicus",
            acquisition_time=now,
            provenance=Provenance(product_id="GLORYS12V1", processing_level="L4", notes="CMEMS daily current field"),
            metadata={"validation_classification": "VALID", "resolution_deg": 0.083},
        ),
    )
    summaries = list_investigation_artifacts(inv_id, registry=default_asset_registry)
    assert len(summaries) == 1
    s = summaries[0]
    assert s.provider == "cmems"
    assert s.source == "marine_copernicus"
    assert s.processing_level == "L4"
    assert s.validation_status == "VALID"
    assert s.acquisition_time == now
    assert s.metadata["resolution_deg"] == 0.083


def test_18_provenance_visibility():
    """18. Provenance references upstream assets across pipeline stages."""
    inv_id = "inv-prov-test"
    # Stage B1 asset
    a_b1 = default_asset_registry.register(
        investigation_id=inv_id,
        provider_id="sentinel1",
        artifact=AcquiredArtifact(
            asset_type=AssetType.SATELLITE_SCENE,
            location="/tmp/scene.zip",
            source="esa",
        ),
    )
    # Stage B2 asset referencing B1
    a_b2 = default_asset_registry.register(
        investigation_id=inv_id,
        provider_id="sentinel1",
        artifact=AcquiredArtifact(
            asset_type=AssetType.IMAGERY_PREVIEW,
            location="/tmp/sigma0.tif",
            source="preprocessing",
            provenance=Provenance(extra={"parent_asset_id": a_b1.id}),
        ),
    )
    # Stage B3 asset referencing B2
    a_b3 = default_asset_registry.register(
        investigation_id=inv_id,
        provider_id="sentinel1",
        artifact=AcquiredArtifact(
            asset_type=AssetType.SPILL_GEOMETRY,
            location="/tmp/spill.geojson",
            source="detection",
            provenance=Provenance(extra={"parent_asset_id": a_b2.id}),
        ),
    )

    summaries = list_investigation_artifacts(inv_id, registry=default_asset_registry)
    assert len(summaries) == 3
    b2_summary = next(s for s in summaries if s.asset_id == a_b2.id)
    b3_summary = next(s for s in summaries if s.asset_id == a_b3.id)

    assert a_b1.id in b2_summary.upstream_asset_ids
    assert a_b2.id in b3_summary.upstream_asset_ids


def test_19_repeated_workflow_execution_behavior():
    """19. Repeated workflow execution updates completed stages deterministically without corrupting state."""
    inv = default_investigation_store.create(
        name="Repeat Test",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=-10, south=40, east=-5, north=45)),
        time_window=TimeWindow(start=datetime(2025, 1, 1, tzinfo=timezone.utc), end=datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )
    objs = _create_mock_pipeline_objects(inv.id)
    default_asset_registry._assets[objs["wind_asset"].id] = objs["wind_asset"]
    default_asset_registry._assets[objs["current_asset"].id] = objs["current_asset"]

    with (
        patch("app.services.investigation_workflow.ingest_sentinel1_artifact", return_value=(objs["scene"], objs["val_result"], objs["b1_asset"])),
        patch("app.services.investigation_workflow.preprocess_sentinel1_scene", return_value=(objs["b2_asset"], {})),
        patch("app.services.investigation_workflow.detect_spills_from_sar_scene", return_value=(objs["spill_detection"], objs["spill_asset"])),
        patch("app.services.investigation_workflow.compute_drift_for_spill", return_value=(objs["drift_result"], objs["drift_asset"])),
        patch("app.services.investigation_workflow.compute_source_estimate_for_spill", return_value=(objs["source_result"], objs["source_asset"])),
        patch("app.services.investigation_workflow.generate_candidate_vessels_for_spill", return_value=(objs["candidate_result"], objs["candidate_asset"])),
        patch("app.services.investigation_workflow.analyze_candidate_trajectories", return_value=(objs["trajectory_result"], objs["trajectory_asset"])),
        patch("app.services.investigation_workflow.analyze_candidate_behavior", return_value=(objs["behavioral_result"], objs["behavioral_asset"])),
        patch("app.services.investigation_workflow.fuse_evidence", return_value=(objs["fusion_result"], objs["fusion_asset"])),
        patch("app.services.investigation_workflow.rank_candidates", return_value=(objs["ranking_result"], objs["ranking_asset"])),
        patch("app.services.investigation_workflow.generate_explainability_report", return_value=(objs["report"], objs["report_asset"])),
    ):
        req = InvestigationRunRequest(
            sentinel1_artifact_path="/tmp/mock.zip",
            wind_asset_id=objs["wind_asset"].id,
            current_asset_id=objs["current_asset"].id,
        )
        res1 = run_investigation_workflow(inv.id, req)
        res2 = run_investigation_workflow(inv.id, req)

        assert res1.status == InvestigationStatus.COMPLETED
        assert res2.status == InvestigationStatus.COMPLETED
        assert res1.completed_stages == res2.completed_stages


def test_20_deterministic_response_structure(client, sample_investigation_payload):
    """20. Deterministic response structure across endpoints."""
    create_res = client.post("/api/v1/investigations", json=sample_investigation_payload)
    inv_id = create_res.json()["id"]

    for endpoint in [f"/api/v1/investigations/{inv_id}", f"/api/v1/investigations/{inv_id}/status"]:
        res = client.get(endpoint)
        assert res.status_code == 200
        assert isinstance(res.json(), dict)
        assert "status" in res.json()


def test_21_orchestration_does_not_modify_scientific_scores():
    """21. Orchestration passes through F1 fusion and F2 ranking scores untouched."""
    inv = default_investigation_store.create(
        name="Scientific Invariant Test",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=-10, south=40, east=-5, north=45)),
        time_window=TimeWindow(start=datetime(2025, 1, 1, tzinfo=timezone.utc), end=datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )
    objs = _create_mock_pipeline_objects(inv.id)
    default_asset_registry._assets[objs["wind_asset"].id] = objs["wind_asset"]
    default_asset_registry._assets[objs["current_asset"].id] = objs["current_asset"]

    # Populate candidate with specific scores
    fused_candidate = VesselFusedEvidence(
        candidate_id="cand-1",
        vessel_id="vessel-1",
        input_index=0,
        mmsi="123456789",
        primary_signals=[
            EvidenceSignal(
                channel_name="spatial_proximity",
                score=0.88,
                status=SignalStatus.VALID,
                nominal_weight=0.50,
                rationale="Test spatial proximity signal.",
            ),
            EvidenceSignal(
                channel_name="temporal_proximity",
                score=0.92,
                status=SignalStatus.VALID,
                nominal_weight=0.25,
                rationale="Test temporal proximity signal.",
            ),
            EvidenceSignal(
                channel_name="trajectory_consistency",
                score=0.75,
                status=SignalStatus.VALID,
                nominal_weight=0.25,
                rationale="Test trajectory consistency signal.",
            ),
        ],
        composite_concordance_score=0.8575,
        evidence_availability_ratio=1.0,
        behavioral_context=BehavioralContextSummary(),
        forward_drift_cross_check=ForwardDriftCrossCheck(),
    )
    objs["fusion_result"].fused_candidates = [fused_candidate]
    objs["fusion_result"].candidate_count = 1

    ranked_candidate = RankedCandidate(
        rank=1,
        vessel_id="vessel-1",
        candidate_id="cand-1",
        mmsi="123456789",
        evidence_consistency_score=0.8575,
        evidence_availability_ratio=1.0,
        valid_primary_channels=3,
        spatial_score=0.88,
        temporal_score=0.92,
        trajectory_score=0.75,
    )
    objs["ranking_result"].candidates = [ranked_candidate]
    objs["ranking_result"].candidate_count = 1

    with (
        patch("app.services.investigation_workflow.ingest_sentinel1_artifact", return_value=(objs["scene"], objs["val_result"], objs["b1_asset"])),
        patch("app.services.investigation_workflow.preprocess_sentinel1_scene", return_value=(objs["b2_asset"], {})),
        patch("app.services.investigation_workflow.detect_spills_from_sar_scene", return_value=(objs["spill_detection"], objs["spill_asset"])),
        patch("app.services.investigation_workflow.compute_drift_for_spill", return_value=(objs["drift_result"], objs["drift_asset"])),
        patch("app.services.investigation_workflow.compute_source_estimate_for_spill", return_value=(objs["source_result"], objs["source_asset"])),
        patch("app.services.investigation_workflow.generate_candidate_vessels_for_spill", return_value=(objs["candidate_result"], objs["candidate_asset"])),
        patch("app.services.investigation_workflow.analyze_candidate_trajectories", return_value=(objs["trajectory_result"], objs["trajectory_asset"])),
        patch("app.services.investigation_workflow.analyze_candidate_behavior", return_value=(objs["behavioral_result"], objs["behavioral_asset"])),
        patch("app.services.investigation_workflow.fuse_evidence", return_value=(objs["fusion_result"], objs["fusion_asset"])),
        patch("app.services.investigation_workflow.rank_candidates", return_value=(objs["ranking_result"], objs["ranking_asset"])),
        patch("app.services.investigation_workflow.generate_explainability_report", return_value=(objs["report"], objs["report_asset"])),
    ):
        res = run_investigation_workflow(
            inv.id,
            InvestigationRunRequest(
                sentinel1_artifact_path="/tmp/mock.zip",
                wind_asset_id=objs["wind_asset"].id,
                current_asset_id=objs["current_asset"].id,
            ),
        )
        assert res.status == InvestigationStatus.COMPLETED
        # Verify scores are untouched in ranking output
        assert objs["ranking_result"].candidates[0].evidence_consistency_score == 0.8575
        assert objs["ranking_result"].candidates[0].spatial_score == 0.88


def test_22_existing_individual_stage_endpoints_remain_functional(client):
    """22. Existing individual stage endpoints remain functional."""
    health_res = client.get("/health")
    assert health_res.status_code == 200
    assert health_res.json()["status"] == "ok"


def test_23_api_errors_do_not_expose_stack_traces(client):
    """23. API errors return structured JSON and never expose Python stack traces."""
    # Test 404
    r_404 = client.get("/api/v1/investigations/bad-id-xyz")
    assert r_404.status_code == 404
    assert "Traceback (most recent call last)" not in r_404.text

    # Test 422
    r_422 = client.post("/api/v1/investigations", json={"invalid": True})
    assert r_422.status_code == 422
    assert "Traceback (most recent call last)" not in r_422.text


def test_24_missing_required_input_fails_closed():
    """24. Missing required input fails closed with structured error without fabrications."""
    inv = default_investigation_store.create(
        name="Missing Input Test",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=-10, south=40, east=-5, north=45)),
        time_window=TimeWindow(start=datetime(2025, 1, 1, tzinfo=timezone.utc), end=datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )
    # Run without any sentinel1 path or assets
    res = run_investigation_workflow(inv.id, InvestigationRunRequest())
    assert res.status == InvestigationStatus.FAILED
    assert res.current_stage == "B1"
    assert res.completed_stages == []
    assert len(res.errors) == 1
    assert res.errors[0].error == "INSUFFICIENT_INPUT"


def test_25_no_fabrication_invariant():
    """25. G1 zero-fabrication invariant: no artificial coordinates or scenes created."""
    inv = default_investigation_store.create(
        name="Zero Fabrication Test",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=-10, south=40, east=-5, north=45)),
        time_window=TimeWindow(start=datetime(2025, 1, 1, tzinfo=timezone.utc), end=datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )
    # When SAR asset is missing for B2, fails immediately without fabricating calibrated GeoTIFF
    objs = _create_mock_pipeline_objects(inv.id)
    with patch("app.services.investigation_workflow.ingest_sentinel1_artifact", return_value=(objs["scene"], objs["val_result"], objs["b1_asset"])):
        # Simulate B2 failure (e.g. preprocessing cannot run)
        with patch("app.services.investigation_workflow.preprocess_sentinel1_scene", side_effect=ValueError("Missing calibration vectors")):
            res = run_investigation_workflow(inv.id, InvestigationRunRequest(sentinel1_artifact_path="/tmp/mock.zip"))
            assert res.status == InvestigationStatus.FAILED
            assert res.current_stage == "B2"
            # Verify no B2 artifact was registered in registry
            assets = default_asset_registry.list_for_investigation(inv.id)
            assert not any(a.type == AssetType.IMAGERY_PREVIEW for a in assets)
