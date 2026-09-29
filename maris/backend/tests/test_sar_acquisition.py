"""Tests for automated Sentinel-1 SAR subscene acquisition service,
CDSE Process API integration, validation contracts, caching, and end-to-end characterization.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds
from starlette.testclient import TestClient

from app.api.experiment_routes import router as experiment_router
from app.core.config import Settings
from app.services.real_experiment.sar_acquisition import (
    SarAcquisitionAuthError,
    SarAcquisitionError,
    SarAcquisitionService,
    SarAcquisitionValidationError,
    SarSubsceneMetadata,
)
from app.services.real_experiment.slick_characterization import characterize_observation
from fastapi import FastAPI


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "sar_subscenes" / "corsica_2018_sar_subscene.tif"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_synthetic_geotiff(
    path: Path,
    crs: str = "EPSG:4326",
    bounds: tuple[float, float, float, float] = (9.3, 43.1, 9.6, 43.4),
    width: int = 100,
    height: int = 100,
    bands: int = 2,
    dtype: str = "float32",
    fill_value: float = -15.0,
) -> None:
    """Create a temporary valid georeferenced GeoTIFF for testing."""
    transform = from_bounds(*bounds, width, height)
    data = np.full((bands, height, width), fill_value, dtype=np.float32)
    # Add a small darker anomaly to simulate backscatter damping
    data[:, 40:60, 40:60] = fill_value - 7.0

    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=bands,
        dtype=dtype,
        crs=crs,
        transform=transform,
    ) as dst:
        for i in range(1, bands + 1):
            dst.write(data[i - 1], i)


# ---------------------------------------------------------------------------
# Test Suite
# ---------------------------------------------------------------------------

class TestSarAcquisitionService:

    def test_resolve_bbox_variants(self, tmp_path: Path):
        """Test resolving bounding boxes from dict, sequence, or centroid."""
        service = SarAcquisitionService()

        # Dict input
        b1 = service._resolve_bbox(bbox={"west": 9.2, "south": 43.0, "east": 9.7, "north": 43.5})
        assert b1 == {"west": 9.2, "south": 43.0, "east": 9.7, "north": 43.5}

        # Sequence input [W, S, E, N]
        b2 = service._resolve_bbox(bbox=[9.1, 42.9, 9.6, 43.4])
        assert b2 == {"west": 9.1, "south": 42.9, "east": 9.6, "north": 43.4}

        # Centroid + radius input
        b3 = service._resolve_bbox(centroid_lon=9.5, centroid_lat=43.2, aoi_radius_km=10.0)
        assert b3["west"] < 9.5 < b3["east"]
        assert b3["south"] < 43.2 < b3["north"]
        assert 0.05 < (b3["east"] - b3["west"]) < 0.35

    def test_unconfigured_auth_error(self, tmp_path: Path):
        """Ensure missing credentials raise SarAcquisitionAuthError for unknown scenes."""
        cfg = Settings(
            cdse_username="",
            cdse_password="",
            cdse_access_token="",
            sar_subscenes_dir=str(tmp_path),
        )
        service = SarAcquisitionService(cfg=cfg)
        assert not service.check_configured()

        with pytest.raises(SarAcquisitionAuthError, match="credentials are not configured"):
            service.acquire_subscene(
                product_id="S1A_IW_GRDH_UNKNOWN_SCENE_999",
                bbox={"west": 10.0, "south": 40.0, "east": 10.5, "north": 40.5},
            )

    def test_validate_sar_geotiff_success(self, tmp_path: Path):
        """Verify validation passes on authentic test fixture."""
        assert FIXTURE_PATH.exists()
        service = SarAcquisitionService()
        meta = service.validate_sar_geotiff(FIXTURE_PATH)
        assert meta["crs"] == "EPSG:4326"
        assert meta["width"] > 0
        assert meta["height"] > 0
        assert meta["count"] == 2
        assert meta["min_db"] < meta["max_db"]

    def test_validate_sar_geotiff_rejections(self, tmp_path: Path):
        """Verify validation rejects unreferenced or identity transform rasters."""
        service = SarAcquisitionService()

        # Missing CRS
        bad_raster = tmp_path / "no_crs.tif"
        data = np.full((1, 100, 100), -12.0, dtype=np.float32)
        with rasterio.open(
            bad_raster, "w", driver="GTiff", height=100, width=100, count=1, dtype="float32"
        ) as dst:
            dst.write(data[0], 1)

        with pytest.raises(SarAcquisitionValidationError, match="CRS"):
            service.validate_sar_geotiff(bad_raster)

    def test_offline_fallback_corsica_benchmark(self, tmp_path: Path):
        """Verify Cap Corse 2018 benchmark resolves to pre-staged local fixture."""
        cfg = Settings(
            cdse_username="",
            cdse_password="",
            cdse_access_token="",
            sar_subscenes_dir=str(tmp_path),
        )
        service = SarAcquisitionService(cfg=cfg)

        path, meta, is_cached = service.acquire_subscene(
            product_id="S1A_IW_GRDH_1SDV_20181008T053424_20181008T053449_024039_02A08A_6B13",
            bbox={"west": 9.38, "south": 43.12, "east": 9.58, "north": 43.35},
        )

        assert path.exists()
        assert is_cached is True
        assert meta.is_test_fixture is True
        assert "Offline" in meta.source_provider
        assert meta.width_px > 0

    def test_cache_hit_and_reuse(self, tmp_path: Path):
        """Verify that an existing valid subscene in the cache directory is reused."""
        cfg = Settings(
            cdse_username="user@example.com",
            cdse_password="dummy_password",
            sar_subscenes_dir=str(tmp_path),
        )
        service = SarAcquisitionService(cfg=cfg)

        # Pre-populate cache
        bbox = {"west": 9.3, "south": 43.1, "east": 9.6, "north": 43.4}
        cache_key = service._generate_cache_key("S1A_TEST_CACHE_HIT", bbox, 800, 800)
        cached_tif = tmp_path / f"acquired_{cache_key}.tif"
        cached_json = tmp_path / f"acquired_{cache_key}.json"

        _create_synthetic_geotiff(cached_tif, width=800, height=800)
        meta_dict = {
            "product_id": "S1A_TEST_CACHE_HIT",
            "sensing_time": "2024-01-01T12:00:00Z",
            "acquisition_timestamp": "2024-01-01T12:05:00Z",
            "bbox": bbox,
            "width_px": 800,
            "height_px": 800,
            "bands": ["sigma0_db_VV", "sigma0_db_VH"],
            "crs": "EPSG:4326",
            "file_path": str(cached_tif),
            "file_size_bytes": cached_tif.stat().st_size,
            "source_provider": "Copernicus Data Space Ecosystem (Process API)",
            "data_authenticity": "Authentic Sentinel-1 SAR backscatter",
            "is_test_fixture": False,
            "notes": "Cached test entry",
        }
        with open(cached_json, "w", encoding="utf-8") as f:
            json.dump(meta_dict, f)

        # Request acquisition - should hit cache
        path, meta, is_cached = service.acquire_subscene(
            product_id="S1A_TEST_CACHE_HIT",
            bbox=bbox,
            width_px=800,
            height_px=800,
        )

        assert path == cached_tif
        assert is_cached is True
        assert meta.product_id == "S1A_TEST_CACHE_HIT"

    def test_mocked_cdse_process_api_acquisition(self, tmp_path: Path):
        """Verify acquisition flow when CDSE Process API succeeds."""
        cfg = Settings(
            cdse_username="user@example.com",
            cdse_password="dummy_password",
            sar_subscenes_dir=str(tmp_path),
        )
        service = SarAcquisitionService(cfg=cfg)

        # Generate fake binary GeoTIFF bytes to return from mocked HTTP response
        simulated_tif_path = tmp_path / "simulated_response.tif"
        _create_synthetic_geotiff(simulated_tif_path, width=200, height=200)
        with open(simulated_tif_path, "rb") as f:
            simulated_bytes = f.read()

        bbox = {"west": 12.0, "south": 37.0, "east": 12.4, "north": 37.3}

        # Mock token fetch and Process API POST
        with patch.object(service, "_get_access_token", return_value="mock_access_token_123"):
            with patch.object(service, "_execute_process_request", return_value=simulated_bytes):
                path, meta, is_cached = service.acquire_subscene(
                    product_id="S1A_IW_GRDH_MOCKED_SCENE",
                    bbox=bbox,
                    width_px=200,
                    height_px=200,
                )

                assert path.exists()
                assert is_cached is False
                assert meta.is_test_fixture is False
                assert meta.product_id == "S1A_IW_GRDH_MOCKED_SCENE"
                assert "Process API" in meta.source_provider


# ---------------------------------------------------------------------------
# Integration with Stage B3 Characterization & FastAPI
# ---------------------------------------------------------------------------

def test_handoff_acquired_subscene_to_characterization():
    """Verify that an acquired SAR subscene can be characterized by Stage B3 detector."""
    result = characterize_observation(
        product_id="S1A_IW_GRDH_1SDV_20181008T053424_20181008T053449_024039_02A08A_6B13",
        title="S1A_IW_GRDH_1SDV_20181008T053424_20181008T053449_024039_02A08A_6B13",
        sensing_start=datetime(2018, 10, 8, 5, 34, 24, tzinfo=timezone.utc),
        sar_raster_path=str(FIXTURE_PATH),
    )

    assert result["has_physical_raster"] is True
    assert result["detected"] is True
    assert "Adaptive Thresholding" in result["status"]
    assert result["area_km2"] is not None and result["area_km2"] > 0
    assert result["damping_contrast_db"] is not None and result["damping_contrast_db"] > 0
    assert result["slick_geometry"] is not None


def test_api_acquire_subscene_endpoint():
    """Test POST /api/experiment/sentinel/acquire-subscene via TestClient."""
    app = FastAPI()
    app.include_router(experiment_router, prefix="/api/experiment")
    client = TestClient(app)

    # 1. Test offline benchmark request
    resp = client.post(
        "/api/experiment/sentinel/acquire-subscene",
        json={
            "product_id": "S1A_IW_GRDH_1SDV_20181008T053424_20181008T053449_024039_02A08A_6B13",
            "bbox": {"west": 9.38, "south": 43.12, "east": 9.58, "north": 43.35},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["is_cached"] is True
    assert Path(data["sar_raster_path"]).exists()
    assert data["bands"] == ["sigma0_db_VV", "sigma0_db_VH"]
    assert data["crs"] == "EPSG:4326"

    # 2. Test unconfigured scene returns 401 error
    resp_unconfigured = client.post(
        "/api/experiment/sentinel/acquire-subscene",
        json={
            "product_id": "S1B_IW_GRDH_UNKNOWN_9999",
            "bbox": {"west": 10.0, "south": 35.0, "east": 10.5, "north": 35.5},
        },
    )
    # If credentials not set in test environment, expect 401 Unauthorized
    if not SarAcquisitionService().check_configured():
        assert resp_unconfigured.status_code == 401
        assert "Copernicus" in resp_unconfigured.json()["detail"]
