"""Stage G4 — Production Hardening Tests.

Validates:
- A1: Configurable CORS origins with safe defaults and no wildcard
- A3: Structured request logging middleware with caplog (2xx INFO, 4xx WARNING, 5xx ERROR, no body leakage)
- A4: Input validation (max_length on name/description) and filesystem path confinement to settings.data_dir
- A5: Global exception handling returning opaque 500 without leaking tracebacks while preserving deliberate HTTPExceptions
- A6: Health endpoint extension with store_investigation_count
- A8: In-memory store startup warning in lifespan
"""

from datetime import datetime, timezone
import logging
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.main import app
from app.models.common import BBoxAreaOfInterest, BoundingBox, TimeWindow
from app.models.investigation_api import InvestigationCreateRequest, InvestigationRunRequest
from app.services.investigation_workflow import default_investigation_store
from app.acquisition.registry import default_asset_registry


@pytest.fixture(autouse=True)
def clean_stores():
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


def test_g4_a1_cors_configuration(monkeypatch):
    """A1: MARIS_ALLOWED_ORIGINS parses correctly, strips whitespace, ignores wildcards, and falls back to dev defaults."""
    # Custom origins
    monkeypatch.setenv("MARIS_ALLOWED_ORIGINS", "https://maris.example.com, https://app.maris.io, *")
    s = Settings()
    assert s.allowed_origins == ["https://maris.example.com", "https://app.maris.io"]
    assert "*" not in s.allowed_origins

    # Unset falls back to defaults
    monkeypatch.delenv("MARIS_ALLOWED_ORIGINS", raising=False)
    s_default = Settings()
    assert "http://localhost:5173" in s_default.allowed_origins
    assert "http://localhost:3000" in s_default.allowed_origins
    assert "*" not in s_default.allowed_origins


def test_g4_a3_structured_request_logging_2xx_and_4xx(client, caplog):
    """A3: Request logging middleware logs method, path, status, and duration (2xx=INFO, 4xx=WARNING)."""
    with caplog.at_level(logging.INFO, logger="maris.api"):
        resp = client.get("/health")
        assert resp.status_code == 200

    info_records = [r for r in caplog.records if r.levelno == logging.INFO and "GET /health 200" in r.message]
    assert len(info_records) >= 1
    assert "ms" in info_records[0].message

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="maris.api"):
        resp404 = client.get("/api/v1/investigations/non-existent-id")
        assert resp404.status_code == 404

    warning_records = [
        r for r in caplog.records if r.levelno == logging.WARNING and "GET /api/v1/investigations/non-existent-id 404" in r.message
    ]
    assert len(warning_records) >= 1
    assert "ms" in warning_records[0].message


def test_g4_a4_investigation_name_max_length(client):
    """A4: Investigation name exceeding 500 characters is rejected with 422 Unprocessable Entity."""
    payload = {
        "name": "X" * 501,
        "area_of_interest": {
            "kind": "bbox",
            "bbox": {"west": -10.0, "south": 40.0, "east": -5.0, "north": 45.0},
        },
        "time_window": {
            "start": "2025-01-01T00:00:00Z",
            "end": "2025-01-02T00:00:00Z",
        },
    }
    resp = client.post("/api/v1/investigations", json=payload)
    assert resp.status_code == 422
    assert "name" in str(resp.json())


def test_g4_a4_investigation_description_max_length(client):
    """A4: Investigation description exceeding 5000 characters is rejected with 422 Unprocessable Entity."""
    payload = {
        "name": "Valid Investigation Name",
        "description": "D" * 5001,
        "area_of_interest": {
            "kind": "bbox",
            "bbox": {"west": -10.0, "south": 40.0, "east": -5.0, "north": 45.0},
        },
        "time_window": {
            "start": "2025-01-01T00:00:00Z",
            "end": "2025-01-02T00:00:00Z",
        },
    }
    resp = client.post("/api/v1/investigations", json=payload)
    assert resp.status_code == 422
    assert "description" in str(resp.json())


def test_g4_a4_sentinel1_artifact_path_outside_data_dir(client):
    """A4: InvestigationRunRequest sentinel1_artifact_path outside data_dir is rejected with 400 Bad Request."""
    inv = default_investigation_store.create(
        name="Path Confinement Test",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=-10, south=40, east=-5, north=45)),
        time_window=TimeWindow(start=datetime(2025, 1, 1, tzinfo=timezone.utc), end=datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )

    outside_paths = [
        "../../etc/passwd",
        "/etc/shadow",
        "C:\\Windows\\System32\\cmd.exe",
        str(Path(tempfile.gettempdir()) / "malicious.zip"),
    ]

    for bad_path in outside_paths:
        resp = client.post(
            f"/api/v1/investigations/{inv.id}/run",
            json={"sentinel1_artifact_path": bad_path},
        )
        assert resp.status_code == 400
        assert "outside the allowed data directory" in resp.json()["detail"]


def test_g4_a4_sar_asset_path_outside_data_dir(client):
    """A4: SpillDetectRequest sar_asset_path outside data_dir is rejected with 400 Bad Request."""
    outside_paths = [
        "../../secrets.tif",
        "/var/data/outside.tif",
        "C:\\outside.tif",
        str(Path(tempfile.gettempdir()) / "outside.tif"),
    ]

    for bad_path in outside_paths:
        resp = client.post(
            "/api/v1/investigations/inv-test/scenes/scene-001/spill-detect",
            json={"sar_asset_path": bad_path},
        )
        assert resp.status_code == 400
        assert "outside the allowed data directory" in resp.json()["detail"]


def test_g4_a4_valid_in_root_path_accepted(client):
    """A4: Valid in-root paths (relative or absolute within settings.data_dir) pass path confinement."""
    inv = default_investigation_store.create(
        name="Valid In-Root Path Test",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=-10, south=40, east=-5, north=45)),
        time_window=TimeWindow(start=datetime(2025, 1, 1, tzinfo=timezone.utc), end=datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )

    valid_in_root_abs = str(settings.data_dir / "safe_file.zip")
    with patch("app.api.routes.run_investigation_workflow") as mock_run:
        mock_run.return_value = {
            "investigation_id": inv.id,
            "status": "PROCESSING",
            "completed_stages": [],
            "artifacts": {},
            "errors": [],
        }
        resp = client.post(
            f"/api/v1/investigations/{inv.id}/run",
            json={"sentinel1_artifact_path": valid_in_root_abs},
        )
        # Should not be rejected by path validation (HTTP 400)
        assert resp.status_code == 200

    # Spill detect endpoint: valid in-root path that doesn't exist returns 404 (passed path validation)
    resp_detect = client.post(
        "/api/v1/investigations/inv-test/scenes/scene-001/spill-detect",
        json={"sar_asset_path": "non_existent_in_root.tif"},
    )
    assert resp_detect.status_code == 404
    assert "does not exist" in resp_detect.json()["detail"]


def test_g4_a5_global_exception_handler(caplog):
    """A5: Unhandled exceptions return HTTP 500 with opaque message and log server-side traceback."""
    test_client = TestClient(app, raise_server_exceptions=False)

    with patch("app.api.routes.default_investigation_store.list", side_effect=RuntimeError("Database connection dropped")):
        with caplog.at_level(logging.ERROR):
            resp = test_client.get("/api/v1/investigations")

        assert resp.status_code == 500
        assert resp.json() == {"detail": "An unexpected internal error occurred."}
        # Server logs must contain actual exception and traceback
        assert "Database connection dropped" in caplog.text
        assert "Traceback" in caplog.text


def test_g4_a5_deliberate_http_exception_preserved(client):
    """A5: Deliberate HTTPExceptions (e.g. 404, 409) return their intended status and details."""
    resp404 = client.get("/api/v1/investigations/missing-investigation-id")
    assert resp404.status_code == 404
    assert resp404.json()["detail"] == "Investigation 'missing-investigation-id' not found"


def test_g4_a6_health_endpoint_store_count(client):
    """A6: GET /health returns status, service, and store_investigation_count."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == settings.service_name
    assert "store_investigation_count" in data
    assert data["store_investigation_count"] == 0

    default_investigation_store.create(
        name="Health Test Investigation",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=-10, south=40, east=-5, north=45)),
        time_window=TimeWindow(start=datetime(2025, 1, 1, tzinfo=timezone.utc), end=datetime(2025, 1, 2, tzinfo=timezone.utc)),
    )

    resp2 = client.get("/health")
    assert resp2.status_code == 200
    assert resp2.json()["store_investigation_count"] == 1


def test_g4_a8_in_memory_startup_warning(caplog):
    """A8: Lifespan logs a startup WARNING explicitly stating the store is in-memory and ephemeral."""
    with caplog.at_level(logging.WARNING, logger="maris.api"):
        with TestClient(app):
            pass

    warning_records = [
        r for r in caplog.records if r.levelno == logging.WARNING and "Investigation store is in-memory and ephemeral" in r.message
    ]
    assert len(warning_records) >= 1
    assert "lost on server restart" in warning_records[0].message
