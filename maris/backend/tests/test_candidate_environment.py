"""Unit and integration tests for Stage C2 Candidate-Level Environmental Context & SAR Lookalike Gating."""

from datetime import datetime, timezone
import math
from typing import Any

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from app.services.candidate_environment import (
    CALM_WATER_THRESHOLD_MS,
    HIGH_WIND_THRESHOLD_MS,
    CandidateEnvironmentSampler,
    WindRegime,
    classify_wind_regime,
    enrich_geojson_feature_collection,
    sample_candidate_environment,
)
from app.services.spill_detection.base import SpillDetectionResult, SpillRegionStats


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_era5_dataset() -> xr.Dataset:
    """Create a synthetic multi-step ERA5 wind NetCDF dataset."""
    times = pd.date_range("2018-10-08 16:00:00", "2018-10-08 19:00:00", freq="1h")
    # ERA5 latitudes are descending
    lats = np.array([45.0, 44.0, 43.0], dtype=np.float32)
    lons = np.array([8.0, 9.0, 10.0], dtype=np.float32)

    # u10: varies with time and longitude
    # At 17:00, (44.0, 9.0): u10 = -2.0, v10 = 0.0
    # At 18:00, (44.0, 9.0): u10 = -4.0, v10 = 0.0
    nt, nlat, nlon = len(times), len(lats), len(lons)
    u_data = np.zeros((nt, nlat, nlon), dtype=np.float32)
    v_data = np.zeros((nt, nlat, nlon), dtype=np.float32)

    # Time step 0: 16:00
    u_data[0, :, :] = -1.0
    v_data[0, :, :] = 0.0
    # Time step 1: 17:00
    u_data[1, :, :] = -2.0
    v_data[1, :, :] = 0.0
    # Time step 2: 18:00
    u_data[2, :, :] = -4.0
    v_data[2, :, :] = 0.0
    # Time step 3: 19:00
    u_data[3, :, :] = -5.0
    v_data[3, :, :] = 0.0

    ds = xr.Dataset(
        data_vars={
            "u10": (["time", "latitude", "longitude"], u_data),
            "v10": (["time", "latitude", "longitude"], v_data),
        },
        coords={
            "time": times,
            "latitude": lats,
            "longitude": lons,
        },
    )
    return ds


@pytest.fixture
def sample_cmems_dataset() -> xr.Dataset:
    """Create a synthetic CMEMS daily ocean current dataset."""
    times = pd.date_range("2018-10-08 00:00:00", periods=2, freq="1D")
    lats = np.array([43.0, 44.0, 45.0], dtype=np.float32)
    lons = np.array([8.0, 9.0, 10.0], dtype=np.float32)

    uo_data = np.full((2, 3, 3), 0.05, dtype=np.float32)
    vo_data = np.full((2, 3, 3), 0.08, dtype=np.float32)

    # Mask one coastal cell with NaN
    uo_data[:, 2, 0] = np.nan
    vo_data[:, 2, 0] = np.nan

    ds = xr.Dataset(
        data_vars={
            "uo": (["time", "latitude", "longitude"], uo_data),
            "vo": (["time", "latitude", "longitude"], vo_data),
        },
        coords={
            "time": times,
            "latitude": lats,
            "longitude": lons,
        },
    )
    return ds


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_1_candidate_centroid_sampling(sample_era5_dataset, sample_cmems_dataset):
    """1. Candidate centroid is sampled correctly."""
    sensing_time = datetime(2018, 10, 8, 17, 0, 0, tzinfo=timezone.utc)
    sampler = CandidateEnvironmentSampler(
        sensing_time=sensing_time,
        era5_source=sample_era5_dataset,
        cmems_source=sample_cmems_dataset,
    )

    env = sampler.sample_point(lat=44.0, lon=9.0)
    assert env.data_quality.wind_available is True
    assert env.data_quality.current_available is True
    assert env.wind.speed_ms == 2.0
    assert env.wind.u10_ms == -2.0
    assert env.wind.v10_ms == 0.0


def test_2_era5_spatial_interpolation(sample_cmems_dataset):
    """2. ERA5 bilinear spatial interpolation works for sub-grid locations."""
    times = pd.date_range("2018-10-08 17:00:00", periods=1, freq="1h")
    lats = np.array([45.0, 43.0], dtype=np.float32)
    lons = np.array([8.0, 10.0], dtype=np.float32)

    # Linear gradient: u = 2.0 at lon 8.0, u = 4.0 at lon 10.0
    u_data = np.zeros((1, 2, 2), dtype=np.float32)
    u_data[0, :, 0] = 2.0
    u_data[0, :, 1] = 4.0
    v_data = np.zeros((1, 2, 2), dtype=np.float32)

    ds = xr.Dataset(
        data_vars={"u10": (["time", "latitude", "longitude"], u_data), "v10": (["time", "latitude", "longitude"], v_data)},
        coords={"time": times, "latitude": lats, "longitude": lons},
    )

    sampler = CandidateEnvironmentSampler(
        sensing_time=datetime(2018, 10, 8, 17, 0, 0, tzinfo=timezone.utc),
        era5_source=ds,
    )

    # Midpoint lon = 9.0 should have u = 3.0
    env = sampler.sample_point(lat=44.0, lon=9.0)
    assert env.wind.u10_ms == pytest.approx(3.0, abs=1e-3)
    assert env.wind.speed_ms == pytest.approx(3.0, abs=1e-3)


def test_3_era5_temporal_interpolation(sample_era5_dataset):
    """3. ERA5 temporal interpolation computes exact intermediate values."""
    # S1 sensing time at 17:30 (halfway between 17:00 u=-2.0 and 18:00 u=-4.0)
    sensing_time = datetime(2018, 10, 8, 17, 30, 0, tzinfo=timezone.utc)
    sampler = CandidateEnvironmentSampler(
        sensing_time=sensing_time,
        era5_source=sample_era5_dataset,
    )

    env = sampler.sample_point(lat=44.0, lon=9.0)
    assert env.data_quality.wind_available is True
    # At 17:30, u should be -3.0
    assert env.wind.u10_ms == pytest.approx(-3.0, abs=1e-3)
    assert env.wind.v10_ms == pytest.approx(0.0, abs=1e-3)
    assert env.wind.speed_ms == pytest.approx(3.0, abs=1e-3)
    assert "linear_vector_interpolation" in (env.wind.temporal_interpolation or "")


def test_4_vector_interpolation_before_magnitude_derivation():
    """4. Vector interpolation occurs before speed/direction derivation."""
    times = pd.date_range("2018-10-08 17:00:00", periods=2, freq="1h")
    lats = np.array([45.0, 43.0], dtype=np.float32)
    lons = np.array([8.0, 10.0], dtype=np.float32)

    # Opposing vectors:
    # t1: u = 4.0, v = 0.0 (speed = 4.0)
    # t2: u = -4.0, v = 0.0 (speed = 4.0)
    u_data = np.zeros((2, 2, 2), dtype=np.float32)
    u_data[0, :, :] = 4.0
    u_data[1, :, :] = -4.0
    v_data = np.zeros((2, 2, 2), dtype=np.float32)

    ds = xr.Dataset(
        data_vars={"u10": (["time", "latitude", "longitude"], u_data), "v10": (["time", "latitude", "longitude"], v_data)},
        coords={"time": times, "latitude": lats, "longitude": lons},
    )

    # At 17:30 (halfway), vector sum is u = 0, v = 0 => speed = 0.0 m/s
    # If scalar interpolation was incorrectly used, speed would be (4 + 4)/2 = 4.0 m/s
    sampler = CandidateEnvironmentSampler(
        sensing_time=datetime(2018, 10, 8, 17, 30, 0, tzinfo=timezone.utc),
        era5_source=ds,
    )
    env = sampler.sample_point(lat=44.0, lon=9.0)
    assert env.wind.u10_ms == pytest.approx(0.0, abs=1e-3)
    assert env.wind.speed_ms == pytest.approx(0.0, abs=1e-3)


def test_5_wind_direction_meteorological_from_convention():
    """5. Wind direction adheres strictly to the meteorological 'FROM' convention."""
    times = pd.date_range("2018-10-08 17:00:00", periods=1, freq="1h")
    lats = np.array([45.0, 43.0], dtype=np.float32)
    lons = np.array([8.0, 10.0], dtype=np.float32)

    cases = [
        # (u, v, expected_from_deg)
        (0.0, -5.0, 0.0),    # Blowing south -> FROM North (0 deg)
        (-5.0, 0.0, 90.0),   # Blowing west -> FROM East (90 deg)
        (0.0, 5.0, 180.0),   # Blowing north -> FROM South (180 deg)
        (5.0, 0.0, 270.0),   # Blowing east -> FROM West (270 deg)
    ]

    for u_val, v_val, expected_deg in cases:
        u_arr = np.full((1, 2, 2), u_val, dtype=np.float32)
        v_arr = np.full((1, 2, 2), v_val, dtype=np.float32)
        ds = xr.Dataset(
            data_vars={"u10": (["time", "latitude", "longitude"], u_arr), "v10": (["time", "latitude", "longitude"], v_arr)},
            coords={"time": times, "latitude": lats, "longitude": lons},
        )
        sampler = CandidateEnvironmentSampler(
            sensing_time=datetime(2018, 10, 8, 17, 0, 0, tzinfo=timezone.utc),
            era5_source=ds,
        )
        env = sampler.sample_point(lat=44.0, lon=9.0)
        assert env.wind.direction_from_deg == pytest.approx(expected_deg, abs=0.1)


def test_6_cmems_current_sampling(sample_cmems_dataset):
    """6. CMEMS near-surface ocean current sampling works."""
    sampler = CandidateEnvironmentSampler(
        sensing_time=datetime(2018, 10, 8, 17, 22, 10, tzinfo=timezone.utc),
        cmems_source=sample_cmems_dataset,
    )
    env = sampler.sample_point(lat=43.5, lon=9.5)
    assert env.data_quality.current_available is True
    assert env.current.uo_ms == pytest.approx(0.05, abs=1e-3)
    assert env.current.vo_ms == pytest.approx(0.08, abs=1e-3)
    expected_speed = math.hypot(0.05, 0.08)
    assert env.current.speed_ms == pytest.approx(expected_speed, abs=1e-3)
    assert env.current.direction_to_deg is not None


def test_7_nan_cmems_cell_handled_safely(sample_cmems_dataset):
    """7. NaN CMEMS ocean cell (e.g. land mask) is handled without crash."""
    sampler = CandidateEnvironmentSampler(
        sensing_time=datetime(2018, 10, 8, 17, 22, 10, tzinfo=timezone.utc),
        cmems_source=sample_cmems_dataset,
    )
    # Coordinate (45.0, 8.0) was masked with NaN in fixture
    env = sampler.sample_point(lat=45.0, lon=8.0)
    assert env.data_quality.current_available is False
    assert env.current.speed_ms is None
    assert any("nearshore/land-masked cell" in w for w in env.data_quality.warnings)


def test_8_candidate_outside_coverage_fails_safely(sample_era5_dataset):
    """8. Candidate outside environmental spatial coverage fails safely without extrapolation."""
    sampler = CandidateEnvironmentSampler(
        sensing_time=datetime(2018, 10, 8, 17, 0, 0, tzinfo=timezone.utc),
        era5_source=sample_era5_dataset,
    )
    # lat 55.0 is far outside domain [43.0, 45.0]
    env = sampler.sample_point(lat=55.0, lon=9.0)
    assert env.data_quality.outside_coverage is True
    assert env.data_quality.wind_available is False
    assert env.regime.classification == WindRegime.UNKNOWN
    assert any("outside ERA5 domain" in w for w in env.data_quality.warnings)


def test_9_missing_era5_produces_structured_unavailable_state():
    """9. Missing ERA5 provider produces clean structured unavailable state."""
    sampler = CandidateEnvironmentSampler(
        sensing_time=datetime(2018, 10, 8, 17, 0, 0, tzinfo=timezone.utc),
        era5_source=None,
    )
    env = sampler.sample_point(lat=44.0, lon=9.0)
    assert env.data_quality.wind_available is False
    assert env.regime.classification == WindRegime.UNKNOWN
    assert any("ERA5 wind dataset is unavailable" in w for w in env.data_quality.warnings)


def test_10_missing_cmems_does_not_remove_wind_evidence(sample_era5_dataset):
    """10. Missing CMEMS ocean current does NOT invalidate wind evidence."""
    sampler = CandidateEnvironmentSampler(
        sensing_time=datetime(2018, 10, 8, 17, 0, 0, tzinfo=timezone.utc),
        era5_source=sample_era5_dataset,
        cmems_source=None,
    )
    env = sampler.sample_point(lat=44.0, lon=9.0)
    assert env.data_quality.wind_available is True
    assert env.data_quality.current_available is False
    assert env.wind.speed_ms == 2.0
    assert any("CMEMS ocean current dataset is unavailable" in w for w in env.data_quality.warnings)


def test_11_calm_water_lookalike_classification():
    """11. Wind speed < 2.5 m/s classifies as CALM_WATER_LOOKALIKE."""
    assessment = classify_wind_regime(2.1)
    assert assessment.classification == WindRegime.CALM_WATER_LOOKALIKE
    assert assessment.damping_consistent is False
    assert assessment.lookalike_risk == "HIGH_LOOKALIKE_PROBABILITY"
    assert "calm-sea dark anomalies are a plausible SAR look-alike" in assessment.evidence_text


def test_12_favorable_detection_window_classification():
    """12. Wind speed between 2.5 and 12.0 m/s classifies as FAVORABLE_DETECTION_WINDOW."""
    assessment = classify_wind_regime(5.5)
    assert assessment.classification == WindRegime.FAVORABLE_DETECTION_WINDOW
    assert assessment.damping_consistent is True
    assert assessment.lookalike_risk == "LOW_LOOKALIKE_PROBABILITY"
    assert "observable SAR contrast" in assessment.evidence_text

    # Boundary cases
    assert classify_wind_regime(2.5).classification == WindRegime.FAVORABLE_DETECTION_WINDOW
    assert classify_wind_regime(12.0).classification == WindRegime.FAVORABLE_DETECTION_WINDOW


def test_13_high_wind_dispersion_classification():
    """13. Wind speed > 12.0 m/s classifies as HIGH_WIND_DISPERSION."""
    assessment = classify_wind_regime(13.5)
    assert assessment.classification == WindRegime.HIGH_WIND_DISPERSION
    assert assessment.damping_consistent is False
    assert assessment.lookalike_risk == "HIGH_DISPERSION_RISK"
    assert "disperse surface slick signatures" in assessment.evidence_text


def test_14_existing_b3_confidence_remains_unchanged(sample_era5_dataset):
    """14. Existing B3 detector confidence remains strictly untouched by C2 enrichment."""
    raw_feature_collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [9.0, 44.0]},
                "properties": {
                    "region_id": 1,
                    "confidence": 0.8842,
                    "centroid": [9.0, 44.0],
                },
            }
        ],
        "properties": {
            "detected": True,
            "confidence": 0.8842,
        },
    }

    sensing_time = datetime(2018, 10, 8, 17, 0, 0, tzinfo=timezone.utc)
    enriched = enrich_geojson_feature_collection(
        raw_feature_collection,
        sensing_time=sensing_time,
        era5_source=sample_era5_dataset,
    )

    feat = enriched["features"][0]
    # Detector confidence MUST remain identical
    assert feat["properties"]["confidence"] == 0.8842
    assert enriched["properties"]["confidence"] == 0.8842
    # Environmental evidence attached
    assert "environment" in feat["properties"]
    assert feat["properties"]["wind_speed_ms"] == 2.0
    assert feat["properties"]["wind_regime"] == WindRegime.CALM_WATER_LOOKALIKE.value


def test_15_batch_sampling_candidates_interface(sample_era5_dataset, sample_cmems_dataset):
    """15. Batch candidate sampling interface accepts SpillRegionStats and tuples."""
    sensing_time = datetime(2018, 10, 8, 17, 0, 0, tzinfo=timezone.utc)

    # 1. Tuples (lat, lon)
    results = sample_candidate_environment(
        candidates=[(44.0, 9.0), (43.5, 9.5)],
        sensing_time=sensing_time,
        era5_source=sample_era5_dataset,
        cmems_source=sample_cmems_dataset,
    )
    assert len(results) == 2
    assert results[0].data_quality.wind_available is True
    assert results[1].data_quality.wind_available is True

    # 2. SpillRegionStats
    region = SpillRegionStats(
        region_id=42,
        pixel_count=100,
        area_m2=100000.0,
        area_km2=0.1,
        min_db=-22.0,
        mean_db=-18.0,
        background_mean_db=-12.0,
        damping_contrast_db=6.0,
        confidence=0.91,
        bbox=(8.9, 43.9, 9.1, 44.1),
        centroid=(9.0, 44.0),  # (lon, lat)
        geometry={"type": "Polygon", "coordinates": []},
    )

    results_reg = sample_candidate_environment(
        candidates=[region],
        sensing_time=sensing_time,
        era5_source=sample_era5_dataset,
    )
    assert len(results_reg) == 1
    assert results_reg[0].wind.speed_ms == 2.0


def test_16_api_enrich_spill_candidates_environment(tmp_path, sample_era5_dataset):
    """16. API endpoint POST /api/v1/investigations/{id}/spills/{id}/candidates/environment works."""
    import json
    from starlette.testclient import TestClient
    from app.main import app
    from app.acquisition.registry import default_asset_registry
    from app.models.asset import Asset
    from app.models.common import AreaOfInterest, AssetType, BBoxAreaOfInterest, BoundingBox, Provenance, TimeWindow
    from app.services.investigation_workflow import default_investigation_store

    client = TestClient(app)

    # 1. Setup store & registry
    inv = default_investigation_store.create(
        name="Test API Spill Candidates",
        area_of_interest=BBoxAreaOfInterest(bbox=BoundingBox(west=8.0, south=43.0, east=10.0, north=45.0)),
        time_window=TimeWindow(
            start=datetime(2018, 10, 8, 0, 0, 0, tzinfo=timezone.utc),
            end=datetime(2018, 10, 9, 0, 0, 0, tzinfo=timezone.utc),
        ),
    )

    # 2. Save sample NetCDF and GeoJSON to disk
    nc_path = tmp_path / "test_wind.nc"
    sample_era5_dataset.to_netcdf(str(nc_path))

    geojson_path = tmp_path / "spill_geometry.geojson"
    fc_data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [9.0, 44.0]},
                "properties": {
                    "region_id": 1,
                    "confidence": 0.85,
                    "centroid": [9.0, 44.0],
                },
            }
        ],
        "properties": {"detected": True, "confidence": 0.85},
    }
    with open(geojson_path, "w", encoding="utf-8") as f:
        json.dump(fc_data, f)

    wind_asset = Asset(
        id=f"asset-wind-{inv.id}",
        investigation_id=inv.id,
        type=AssetType.ENVIRONMENT_WIND,
        provider="era5",
        source="copernicus_cds",
        location=str(nc_path),
        acquisition_time=datetime(2018, 10, 8, 17, 0, 0, tzinfo=timezone.utc),
    )
    default_asset_registry._assets[wind_asset.id] = wind_asset
    default_asset_registry._order.append(wind_asset.id)

    spill_asset = Asset(
        id=f"asset-spill-{inv.id}",
        investigation_id=inv.id,
        type=AssetType.SPILL_GEOMETRY,
        provider="spill_detection",
        source="sar_processing",
        location=str(geojson_path),
        acquisition_time=datetime(2018, 10, 8, 17, 0, 0, tzinfo=timezone.utc),
        provenance=Provenance(product_id="spill-test-01"),
        metadata={"centroid": {"lon": 9.0, "lat": 44.0}, "detected": True},
    )
    default_asset_registry._assets[spill_asset.id] = spill_asset
    default_asset_registry._order.append(spill_asset.id)

    # 3. Call endpoint
    resp = client.post(
        f"/api/v1/investigations/{inv.id}/spills/{spill_asset.id}/candidates/environment",
        json={"wind_asset_id": wind_asset.id},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["candidate_count"] == 1
    assert "CALM_WATER_LOOKALIKE" in data["regime_counts"]
    feat = data["features"][0]
    assert "environment" in feat["properties"]
    assert feat["properties"]["wind_speed_ms"] == 2.0

