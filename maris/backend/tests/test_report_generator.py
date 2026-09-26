"""Unit and integration tests for Step 8 — Scientific Investigation Report & Export.

Verifies:
- Report data transformation from stored experiment records without re-running models.
- Preservation of authoritative candidate rankings and exact scores.
- Publication-quality PDF generation and header/footer structure.
- API endpoints: /runs/{run_id}/report (PDF and JSON) and /runs/{run_id}/export (JSON).
- Scientific neutrality and limitation disclaimers.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.main import app
from app.services.real_experiment.experiment_store import get_experiment_store
from app.services.report_generator import (
    build_scientific_report_data,
    render_scientific_report_pdf,
)

CORSICA_RUN_ID = "1ed7ac6a-d6ff-4720-b2e4-2eb62939dbb5"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_build_scientific_report_data_corsica_benchmark() -> None:
    """Verify stored historical run converts into structured report without re-running."""
    store = get_experiment_store()
    run = store.get_run(CORSICA_RUN_ID)
    assert run is not None, f"Benchmark run {CORSICA_RUN_ID} must exist in test database"

    data = build_scientific_report_data(run)

    # 1. Metadata
    assert data["metadata"]["run_id"] == CORSICA_RUN_ID
    assert data["metadata"]["model_version"] == run.model_version
    assert data["metadata"]["status"] == "completed"

    # 2. Executive Summary
    exec_sum = data["executive_summary"]
    assert "43.2483°N" in exec_sum["observation_point"]
    assert "9.4783°E" in exec_sum["observation_point"]
    assert exec_sum["source_uncertainty_km"] == 6.5
    assert exec_sum["backtrack_duration_hours"] == 12.0
    assert exec_sum["candidate_count"] == 2
    assert "evidence-consistency assessment" in exec_sum["assessment_statement"].lower()

    # 3. Observation Details
    assert data["observation"]["latitude"] == pytest.approx(43.2483, abs=0.001)
    assert data["observation"]["longitude"] == pytest.approx(9.4783, abs=0.001)

    # 4. Environmental Sources
    assert "ERA5" in data["environment"]["wind_source"]
    assert "CMEMS" in data["environment"]["current_source"]
    assert "Curated" in data["environment"]["ais_source"]

    # 5. Drift Configuration
    assert data["drift_configuration"]["backtrack_duration_hours"] == 12.0
    assert data["drift_configuration"]["backward_steps_count"] == 12

    # 6. Candidate Ordering & Score Invariance
    cands = data["candidates"]
    assert len(cands) == 2
    assert cands[0]["rank"] == 1
    assert cands[0]["vessel_name"] == "MV ULYSSE"
    assert cands[0]["evidence_consistency_score"] == pytest.approx(0.5378, abs=0.001)
    assert cands[1]["rank"] == 2
    assert cands[1]["vessel_name"] == "MEDITERRANEAN STAR"
    assert cands[1]["evidence_consistency_score"] == pytest.approx(0.3920, abs=0.001)

    # 7. Reproducibility & Limitations
    repro = data["reproducibility"]
    assert repro["run_id"] == CORSICA_RUN_ID
    assert repro["result_state"] == "Completed"
    assert len(data["limitations"]) >= 3


def test_render_scientific_report_pdf() -> None:
    """Verify ReportLab compiles report_data into non-empty, valid PDF bytes."""
    store = get_experiment_store()
    run = store.get_run(CORSICA_RUN_ID)
    assert run is not None
    data = build_scientific_report_data(run)

    pdf_bytes = render_scientific_report_pdf(data)
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 2000
    assert pdf_bytes.startswith(b"%PDF-")


def test_api_report_endpoint_json(client: TestClient) -> None:
    """Verify GET /api/experiment/runs/{run_id}/report?format=json returns report data."""
    res = client.get(f"/api/experiment/runs/{CORSICA_RUN_ID}/report?format=json")
    assert res.status_code == 200
    payload = res.json()
    assert payload["metadata"]["run_id"] == CORSICA_RUN_ID
    assert payload["executive_summary"]["candidate_count"] == 2
    assert payload["candidates"][0]["vessel_name"] == "MV ULYSSE"


def test_api_report_endpoint_pdf(client: TestClient) -> None:
    """Verify GET /api/experiment/runs/{run_id}/report?format=pdf returns PDF stream."""
    res = client.get(f"/api/experiment/runs/{CORSICA_RUN_ID}/report?format=pdf")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert "MARIS_Report_" in res.headers["content-disposition"]
    assert res.content.startswith(b"%PDF-")
    assert len(res.content) > 2000


def test_api_export_endpoint_json(client: TestClient) -> None:
    """Verify GET /api/experiment/runs/{run_id}/export returns attachment JSON."""
    res = client.get(f"/api/experiment/runs/{CORSICA_RUN_ID}/export")
    assert res.status_code == 200
    assert "MARIS_Experiment_" in res.headers["content-disposition"]
    payload = res.json()
    assert payload["metadata"]["run_id"] == CORSICA_RUN_ID


def test_api_report_endpoint_not_found(client: TestClient) -> None:
    """Verify 404 for non-existent run ID."""
    res = client.get("/api/experiment/runs/00000000-0000-0000-0000-000000000000/report?format=json")
    assert res.status_code == 404
