"""Tests for genuine SAR subscene raster processing, detector invocation,
georeferencing correctness, and Evaluator integration in MARIS.
"""

import math
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pytest
import rasterio

from app.services.real_experiment.slick_characterization import characterize_observation
from app.services.real_experiment.evaluator_workflow import (
    list_reference_observations,
    get_reference_observation,
    run_evaluator_investigation,
)
from app.services.spill_detection.detector import AdaptiveThresholdSpillDetector
from app.services.spill_detection.geometry import extract_spill_geometries


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "sar_subscenes" / "corsica_2018_sar_subscene.tif"


def test_geotiff_fixture_metadata_and_georeferencing():
    """Verify GeoTIFF test fixture exists, has EPSG:4326 CRS, non-identity transform,
    two polarimetric bands, and explicit test fixture tags.
    """
    assert FIXTURE_PATH.exists(), f"SAR subscene fixture not found at {FIXTURE_PATH}"

    with rasterio.open(FIXTURE_PATH) as src:
        assert src.count == 2, f"Expected 2 bands, got {src.count}"
        assert src.crs is not None and src.crs.to_epsg() == 4326, f"Expected EPSG:4326, got {src.crs}"
        assert src.dtypes[0] == "float32"
        
        # Verify georeferenced bounds match Corsica subscene
        b = src.bounds
        assert 9.35 <= b.left <= 9.40
        assert 9.55 <= b.right <= 9.60
        assert 43.10 <= b.bottom <= 43.16
        assert 43.34 <= b.top <= 43.36
        
        # Verify non-identity transform (degree scale ~ 0.00025 deg)
        assert abs(src.transform.a) > 0 and abs(src.transform.a) < 0.01
        assert abs(src.transform.e) > 0 and abs(src.transform.e) < 0.01
        
        # Verify development test fixture tag
        tags = src.tags()
        assert tags.get("DATASET_TYPE") == "DEVELOPMENT_TEST_FIXTURE"


def test_detector_invocation_arguments():
    """Verify AdaptiveThresholdSpillDetector.detect() receives and processes
    all 4 required arguments (raster, valid_mask, polarization, pixel_size_m) without TypeError.
    """
    with rasterio.open(FIXTURE_PATH) as src:
        arr = src.read(1).astype(np.float32)
        detector = AdaptiveThresholdSpillDetector(damping_threshold_db=3.5, k_sigma=2.0)
        valid_mask = np.isfinite(arr) & (arr > detector.noise_floor_db)
        pixel_size_m = (20.0, 20.0)

        # Call with all 4 required positional/keyword arguments
        det_res = detector.detect(
            raster=arr,
            valid_mask=valid_mask,
            polarization="VV",
            pixel_size_m=pixel_size_m,
        )

        assert det_res is not None
        assert det_res.mask.shape == arr.shape
        assert det_res.mask.dtype == bool
        assert det_res.probability.shape == arr.shape
        assert np.sum(det_res.mask) > 0, "Expected dark-spot spill candidate pixels detected"


def test_spill_geometry_extraction_georeferenced():
    """Verify extract_spill_geometries generates valid GeoJSON in WGS84 geographic space,
    not raw pixel coordinates.
    """
    with rasterio.open(FIXTURE_PATH) as src:
        arr = src.read(1).astype(np.float32)
        detector = AdaptiveThresholdSpillDetector(damping_threshold_db=3.5, k_sigma=2.0)
        valid_mask = np.isfinite(arr) & (arr > detector.noise_floor_db)
        det_res = detector.detect(
            raster=arr,
            valid_mask=valid_mask,
            polarization="VV",
            pixel_size_m=(20.0, 20.0),
        )

        geom_res = extract_spill_geometries(
            mask=det_res.mask,
            probability=det_res.probability,
            raster=arr,
            transform=src.transform,
            crs=src.crs,
            background_mean_db=-9.0,
            min_area_m2=10000.0,
        )

        assert geom_res.detected is True
        assert geom_res.total_area_m2 > 1e6, f"Expected realistic area > 1 km², got {geom_res.total_area_m2}"
        assert geom_res.centroid is not None
        c_lon, c_lat = geom_res.centroid

        # MUST be in geographic degrees (WGS84) around Corsica
        assert 9.4 <= c_lon <= 9.6, f"Centroid lon {c_lon} out of WGS84 bounds"
        assert 43.15 <= c_lat <= 43.35, f"Centroid lat {c_lat} out of WGS84 bounds"

        # Verify GeoJSON polygon coordinates are in WGS84
        geom = geom_res.geometry
        assert geom is not None and geom["type"] in ("Polygon", "MultiPolygon")
        first_ring = geom["coordinates"][0] if geom["type"] == "Polygon" else geom["coordinates"][0][0]
        for pt in first_ring:
            lon, lat = pt[0], pt[1]
            assert 9.0 <= lon <= 10.0, f"Polygon lon {lon} not geographic degree"
            assert 43.0 <= lat <= 44.0, f"Polygon lat {lat} not geographic degree"


def test_characterize_observation_with_raster_priority():
    """Verify that when a real SAR raster path is supplied, characterize_observation
    processes the physical pixels with priority over benchmark values.
    """
    res = characterize_observation(
        product_id="S1B_IW_GRDH_1SDV_20181008T052822_CORSICA_TEST",
        title="Sentinel-1B Corsica Subscene",
        sensing_start="2018-10-08T05:28:22Z",
        sar_raster_path=str(FIXTURE_PATH),
    )

    assert res["detected"] is True
    assert res["has_physical_raster"] is True
    assert "Physical SAR Raster" in res["detection_method"]
    assert "Development Test Fixture" in res["provenance"]
    assert res["area_km2"] > 0
    assert res["pixel_count"] > 1000
    assert res["damping_contrast_db"] > 4.0
    assert 9.4 <= res["centroid_lon"] <= 9.6
    assert 43.2 <= res["centroid_lat"] <= 43.35


def test_characterize_observation_fallback_when_no_raster():
    """Verify that when no SAR raster is supplied, the pipeline safely falls back
    to the verified benchmark characterization without errors.
    """
    res = characterize_observation(
        product_id="S1B_IW_GRDH_1SDV_20181008T052822_BENCHMARK",
        title="Historical Corsica Benchmark",
        sensing_start="2018-10-08T05:28:22Z",
        sar_raster_path=None,
    )

    assert res["detected"] is True
    assert res.get("has_physical_raster") is not True
    # Falls back to benchmark polygon & area
    assert res["area_km2"] == 45.2
    assert res["centroid_lon"] == 9.47833
    assert res["centroid_lat"] == 43.24833
    assert "Verified Benchmark" in res["status"]


def test_evaluator_reference_observations_enriched_with_raster():
    """Verify list_reference_observations and get_reference_observation enrich
    the Corsica reference observation with dynamic SAR detection metrics.
    """
    obs_list = list_reference_observations()
    corsica_obs = next((o for o in obs_list if o["id"] == "ref_corsica_2018"), None)
    assert corsica_obs is not None

    assert corsica_obs["has_physical_raster"] is True
    assert corsica_obs.get("raster_provenance") is not None
    assert corsica_obs.get("detected_slick_metrics") is not None

    metrics = corsica_obs["detected_slick_metrics"]
    assert metrics["detected"] is True
    assert metrics["area_km2"] > 0
    assert metrics["pixel_count"] > 1000
    assert 9.4 <= metrics["centroid_lon"] <= 9.6
    assert 43.2 <= metrics["centroid_lat"] <= 43.35

    # Verify single fetch also enriches
    single_obs = get_reference_observation("ref_corsica_2018")
    assert single_obs is not None
    assert single_obs["has_physical_raster"] is True


def test_evaluator_workflow_investigation_end_to_end():
    """Verify run_evaluator_investigation runs end-to-end with the physical raster
    observation, preserves raster detection metadata, and executes backward drift & attribution.
    """
    record = run_evaluator_investigation(
        selected_image_id="ref_corsica_2018",
        observation_lon=9.491,
        observation_lat=43.274,
        observation_time=datetime(2018, 10, 8, 5, 28, 22, tzinfo=timezone.utc),
        wind_speed_ms=7.2,
        wind_direction_deg=235.0,
        current_speed_ms=0.22,
        current_direction_deg=35.0,
        corridor_km=25.0,
        backtrack_hours=6.0,
        step_hours=0.5,
        spill_area_m2=9440000.0,
    )

    assert record is not None
    assert record["investigation_id"].startswith("inv_eval_")
    assert record["has_physical_raster"] is True
    assert record["detected_slick_metrics"] is not None
    assert "reconstructed_source" in record
    assert "final_attribution" in record
    assert record["final_attribution"]["top_candidate"]["vessel_name"] in ("MV ULYSSE", "CSL VIRGINIA")
