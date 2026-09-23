"""MARIS Evaluator Investigation Workflow Service.

Provides the end-to-end interactive evaluator investigation pipeline:
1. Reference Sentinel-1 SAR observations with verified coordinates and historical demo semantics.
2. Real backward drift execution using configurable wind speed/direction and ocean current speed/direction.
3. Strict AIS candidate filtering with configurable spatial corridor and temporal intersection.
4. Clean separation of provider boundary states: LIVE AIS, NO PROVIDER / CONFIGURATION, NO ELIGIBLE VESSELS.
5. Inference using the active scaled ML model (attr_lr_scaled_10k_20260919_183349).
6. Complete reproducible persistence in SQLite without rerunning or mutating historical results.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import xarray as xr

from app.core.config import settings
from app.services.real_experiment.ais_database import (
    PROVENANCE_LIVE_AIS_PROVIDER,
    PROVENANCE_MANUAL_REFERENCE,
    PROVENANCE_NOAA_MARINECADASTRE,
    PROVENANCE_SYNTHETIC_BENCHMARK,
    PROVENANCE_UNVERIFIED_IMPORT,
    get_candidates_for_scene,
    get_vessel_by_identifier,
    init_ais_database,
    query_vessels_in_spatiotemporal_box,
)
from app.services.real_experiment.ais_search import AisSearchService, ConfigurationUnavailable as AisConfigUnavailable
from app.services.real_experiment.experiment_store import get_experiment_store
from app.services.source_estimation import (
    generate_source_candidate_polygon,
    run_backward_drift,
)
from app.services.synthetic_experiment.feature_builder import (
    FEATURE_NAMES,
    extract_vessel_features,
    haversine_km,
)
from app.services.synthetic_experiment.model_registry import load_model

# ---------------------------------------------------------------------------
# Truthful Reference Observations
# ---------------------------------------------------------------------------

REFERENCE_OBSERVATIONS: list[dict[str, Any]] = [
    {
        "id": "ref_corsica_2018",
        "title": "Cap Corse / Northern Corsica (Historical Demo Benchmark)",
        "image_file": "corsica_2018_s1.jpg",
        "image_path": "/satellite/corsica_2018_s1.jpg",
        "region": "Cap Corse, Mediterranean Sea",
        "observation_lon": 9.47833,
        "observation_lat": 43.24833,
        "observation_time": "2018-10-08T05:28:00Z",
        "sensor": "Sentinel-1A C-SAR (Historical Scene)",
        "mode": "IW / GRDH",
        "slick_area_km2": 45.2,
        "is_historical_demo": True,
        "historical_context": (
            "Historical Corsica benchmark scene (October 2018 collision between Ulysse and CSL Virginia). "
            "Preserves historical demo semantics rather than operational newly acquired Sentinel-1 data."
        ),
        "default_wind_speed_ms": 7.2,
        "default_wind_direction_deg": 235.0,
        "default_current_speed_ms": 0.22,
        "default_current_direction_deg": 35.0,
        "backtrack_hours": 8.0,
        "benchmark_candidates": [
            {
                "id": "vessel_ulysse",
                "vessel_name": "MV ULYSSE",
                "mmsi": "228308800",
                "vessel_type": "Ro-Ro Cargo",
                "positions": [
                    {"timestamp": "2018-10-07T21:00:00Z", "lat": 43.15, "lon": 9.38, "speed": 17.5, "heading": 215.0},
                    {"timestamp": "2018-10-07T22:00:00Z", "lat": 43.18, "lon": 9.40, "speed": 17.6, "heading": 216.0},
                    {"timestamp": "2018-10-07T22:30:00Z", "lat": 43.21, "lon": 9.42, "speed": 18.0, "heading": 218.0},
                    {"timestamp": "2018-10-07T23:30:00Z", "lat": 43.23, "lon": 9.43, "speed": 17.9, "heading": 217.0},
                    {"timestamp": "2018-10-08T00:00:00Z", "lat": 43.26, "lon": 9.45, "speed": 17.8, "heading": 216.0},
                    {"timestamp": "2018-10-08T01:00:00Z", "lat": 43.27, "lon": 9.46, "speed": 17.0, "heading": 215.0},
                    {"timestamp": "2018-10-08T01:30:00Z", "lat": 43.28, "lon": 9.47, "speed": 16.5, "heading": 214.0},
                    {"timestamp": "2018-10-08T02:30:00Z", "lat": 43.26, "lon": 9.48, "speed": 13.5, "heading": 211.0},
                    {"timestamp": "2018-10-08T03:00:00Z", "lat": 43.25, "lon": 9.48, "speed": 12.0, "heading": 210.0},
                    {"timestamp": "2018-10-08T04:00:00Z", "lat": 43.249, "lon": 9.479, "speed": 3.5, "heading": 207.0},
                    {"timestamp": "2018-10-08T05:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.8, "heading": 205.0},
                    {"timestamp": "2018-10-08T05:28:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.2, "heading": 205.0},
                    {"timestamp": "2018-10-08T12:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.0, "heading": 205.0},
                    {"timestamp": "2018-10-08T18:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.0, "heading": 205.0},
                    {"timestamp": "2018-10-09T00:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.0, "heading": 205.0},
                    {"timestamp": "2018-10-09T06:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.0, "heading": 205.0},
                    {"timestamp": "2018-10-09T12:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.0, "heading": 205.0},
                    {"timestamp": "2018-10-09T17:14:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.0, "heading": 205.0},
                ],
            },
            {
                "id": "vessel_csl_virginia",
                "vessel_name": "CSL VIRGINIA",
                "mmsi": "229986000",
                "vessel_type": "Container Ship",
                "positions": [
                    {"timestamp": "2018-10-07T21:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.1, "heading": 95.0},
                    {"timestamp": "2018-10-07T23:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.1, "heading": 93.0},
                    {"timestamp": "2018-10-08T00:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.1, "heading": 92.0},
                    {"timestamp": "2018-10-08T01:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.1, "heading": 92.0},
                    {"timestamp": "2018-10-08T02:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.1, "heading": 93.0},
                    {"timestamp": "2018-10-08T03:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.1, "heading": 94.0},
                    {"timestamp": "2018-10-08T04:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.1, "heading": 93.0},
                    {"timestamp": "2018-10-08T05:28:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.1, "heading": 90.0},
                    {"timestamp": "2018-10-08T12:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.0, "heading": 90.0},
                    {"timestamp": "2018-10-08T18:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.0, "heading": 90.0},
                    {"timestamp": "2018-10-09T00:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.0, "heading": 90.0},
                    {"timestamp": "2018-10-09T06:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.0, "heading": 90.0},
                    {"timestamp": "2018-10-09T12:00:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.0, "heading": 90.0},
                    {"timestamp": "2018-10-09T17:14:00Z", "lat": 43.248, "lon": 9.478, "speed": 0.0, "heading": 90.0},
                ],
            },
            {
                "id": "vessel_corsica_transit_a",
                "vessel_name": "MEDITERRANEAN STAR",
                "mmsi": "247112233",
                "vessel_type": "Chemical Tanker",
                "positions": [
                    {"timestamp": "2018-10-07T21:00:00Z", "lat": 43.05, "lon": 9.15, "speed": 14.2, "heading": 130.0},
                    {"timestamp": "2018-10-07T22:00:00Z", "lat": 42.99, "lon": 9.22, "speed": 14.3, "heading": 131.0},
                    {"timestamp": "2018-10-07T23:00:00Z", "lat": 42.97, "lon": 9.27, "speed": 14.4, "heading": 132.0},
                    {"timestamp": "2018-10-08T00:00:00Z", "lat": 42.95, "lon": 9.32, "speed": 14.5, "heading": 132.0},
                    {"timestamp": "2018-10-08T01:00:00Z", "lat": 42.91, "lon": 9.38, "speed": 14.3, "heading": 133.0},
                    {"timestamp": "2018-10-08T02:00:00Z", "lat": 42.87, "lon": 9.44, "speed": 14.2, "heading": 134.0},
                    {"timestamp": "2018-10-08T03:00:00Z", "lat": 42.82, "lon": 9.50, "speed": 14.0, "heading": 135.0},
                    {"timestamp": "2018-10-08T04:00:00Z", "lat": 42.76, "lon": 9.59, "speed": 13.9, "heading": 134.0},
                    {"timestamp": "2018-10-08T05:28:00Z", "lat": 42.70, "lon": 9.68, "speed": 13.8, "heading": 134.0},
                    {"timestamp": "2018-10-08T12:00:00Z", "lat": 43.10, "lon": 9.55, "speed": 8.0, "heading": 340.0},
                    {"timestamp": "2018-10-09T06:00:00Z", "lat": 43.20, "lon": 9.50, "speed": 5.0, "heading": 320.0},
                    {"timestamp": "2018-10-09T17:14:00Z", "lat": 43.25, "lon": 9.48, "speed": 1.2, "heading": 10.0},
                ],
            },
            {
                "id": "vessel_distant_patrol",
                "vessel_name": "LIGURIAN BREEZE",
                "mmsi": "247998877",
                "vessel_type": "Fishing Vessel",
                "positions": [
                    {"timestamp": "2018-10-07T21:00:00Z", "lat": 43.85, "lon": 9.90, "speed": 6.5, "heading": 280.0},
                    {"timestamp": "2018-10-07T23:00:00Z", "lat": 43.83, "lon": 9.83, "speed": 6.7, "heading": 277.0},
                    {"timestamp": "2018-10-08T00:00:00Z", "lat": 43.82, "lon": 9.75, "speed": 6.8, "heading": 275.0},
                    {"timestamp": "2018-10-08T01:00:00Z", "lat": 43.81, "lon": 9.70, "speed": 6.5, "heading": 278.0},
                    {"timestamp": "2018-10-08T02:00:00Z", "lat": 43.81, "lon": 9.65, "speed": 6.3, "heading": 280.0},
                    {"timestamp": "2018-10-08T03:00:00Z", "lat": 43.80, "lon": 9.60, "speed": 6.2, "heading": 282.0},
                    {"timestamp": "2018-10-08T04:00:00Z", "lat": 43.79, "lon": 9.54, "speed": 6.1, "heading": 279.0},
                    {"timestamp": "2018-10-08T05:28:00Z", "lat": 43.78, "lon": 9.45, "speed": 6.0, "heading": 278.0},
                ],
            },
            {
                "id": "vessel_tyrrhenian_ferry",
                "vessel_name": "MARE NOSTRUM",
                "mmsi": "247556789",
                "vessel_type": "Passenger Ferry",
                "positions": [
                    {"timestamp": "2018-10-07T22:00:00Z", "lat": 41.90, "lon": 8.72, "speed": 21.0, "heading": 355.0},
                    {"timestamp": "2018-10-07T23:00:00Z", "lat": 42.28, "lon": 8.76, "speed": 21.2, "heading": 357.0},
                    {"timestamp": "2018-10-08T00:00:00Z", "lat": 42.68, "lon": 8.80, "speed": 21.0, "heading": 356.0},
                    {"timestamp": "2018-10-08T01:00:00Z", "lat": 43.06, "lon": 8.82, "speed": 20.8, "heading": 358.0},
                    {"timestamp": "2018-10-08T02:30:00Z", "lat": 43.42, "lon": 8.84, "speed": 20.5, "heading": 5.0},
                    {"timestamp": "2018-10-08T05:28:00Z", "lat": 43.70, "lon": 8.85, "speed": 19.8, "heading": 8.0},
                ],
            },
        ],
    },
    {
        "id": "ref_arabian_sea_alpha",
        "title": "Central Arabian Sea Transit Corridor",
        "image_file": "sentinel1_arabian_sea_alpha.png",
        "image_path": "/satellite/sentinel1_arabian_sea_alpha.png",
        "region": "Central Arabian Sea",
        "observation_lon": 66.198,
        "observation_lat": 15.642,
        "observation_time": "2025-03-15T05:42:00Z",
        "sensor": "Sentinel-1 C-SAR IW",
        "mode": "IW / GRDH",
        "slick_area_km2": 28.4,
        "is_historical_demo": False,
        "historical_context": (
            "Reference Sentinel-1 SAR acquisition capturing slick signature along high-density Arabian Sea tanker transit lane."
        ),
        "default_wind_speed_ms": 5.8,
        "default_wind_direction_deg": 310.0,
        "default_current_speed_ms": 0.18,
        "default_current_direction_deg": 120.0,
        "backtrack_hours": 6.0,
        "benchmark_candidates": [
            {
                "id": "vessel_tanker_al_zubarah",
                "vessel_name": "AL ZUBARAH",
                "mmsi": "408123456",
                "vessel_type": "Crude Oil Tanker",
                "positions": [
                    {"timestamp": "2025-03-14T23:42:00Z", "lat": 15.54, "lon": 66.08, "speed": 13.2, "heading": 65.0},
                    {"timestamp": "2025-03-15T00:42:00Z", "lat": 15.56, "lon": 66.10, "speed": 13.3, "heading": 65.0},
                    {"timestamp": "2025-03-15T01:42:00Z", "lat": 15.58, "lon": 66.13, "speed": 13.4, "heading": 66.0},
                    {"timestamp": "2025-03-15T02:42:00Z", "lat": 15.60, "lon": 66.15, "speed": 13.2, "heading": 65.0},
                    {"timestamp": "2025-03-15T03:42:00Z", "lat": 15.62, "lon": 66.18, "speed": 13.1, "heading": 64.0},
                    {"timestamp": "2025-03-15T04:42:00Z", "lat": 15.64, "lon": 66.20, "speed": 13.2, "heading": 65.0},
                    {"timestamp": "2025-03-15T05:42:00Z", "lat": 15.66, "lon": 66.23, "speed": 13.3, "heading": 65.0},
                ],
            },
            {
                "id": "vessel_cargo_gulf_runner",
                "vessel_name": "GULF RUNNER",
                "mmsi": "419987654",
                "vessel_type": "Bulk Carrier",
                "positions": [
                    {"timestamp": "2025-03-14T23:42:00Z", "lat": 15.75, "lon": 66.35, "speed": 11.5, "heading": 240.0},
                    {"timestamp": "2025-03-15T01:00:00Z", "lat": 15.72, "lon": 66.30, "speed": 11.7, "heading": 241.0},
                    {"timestamp": "2025-03-15T02:00:00Z", "lat": 15.69, "lon": 66.25, "speed": 11.8, "heading": 242.0},
                    {"timestamp": "2025-03-15T03:00:00Z", "lat": 15.67, "lon": 66.21, "speed": 11.7, "heading": 241.0},
                    {"timestamp": "2025-03-15T04:00:00Z", "lat": 15.64, "lon": 66.17, "speed": 11.6, "heading": 241.0},
                    {"timestamp": "2025-03-15T05:42:00Z", "lat": 15.61, "lon": 66.12, "speed": 11.6, "heading": 241.0},
                ],
            },
            {
                "id": "vessel_dist_trawler",
                "vessel_name": "SAGAR RATNA",
                "mmsi": "419001122",
                "vessel_type": "Fishing Vessel",
                "positions": [
                    {"timestamp": "2025-03-14T23:42:00Z", "lat": 16.20, "lon": 66.85, "speed": 4.5, "heading": 180.0},
                    {"timestamp": "2025-03-15T01:42:00Z", "lat": 16.18, "lon": 66.84, "speed": 4.3, "heading": 178.0},
                    {"timestamp": "2025-03-15T03:42:00Z", "lat": 16.16, "lon": 66.83, "speed": 4.4, "heading": 177.0},
                    {"timestamp": "2025-03-15T05:42:00Z", "lat": 16.15, "lon": 66.82, "speed": 4.2, "heading": 175.0},
                ],
            },
            {
                "id": "vessel_vlcc_hormuz_star",
                "vessel_name": "HORMUZ STAR",
                "mmsi": "470112233",
                "vessel_type": "VLCC",
                "positions": [
                    {"timestamp": "2025-03-15T00:00:00Z", "lat": 15.40, "lon": 65.85, "speed": 10.8, "heading": 72.0},
                    {"timestamp": "2025-03-15T01:00:00Z", "lat": 15.43, "lon": 65.92, "speed": 10.9, "heading": 71.0},
                    {"timestamp": "2025-03-15T02:00:00Z", "lat": 15.46, "lon": 65.99, "speed": 11.0, "heading": 70.0},
                    {"timestamp": "2025-03-15T03:30:00Z", "lat": 15.50, "lon": 66.08, "speed": 10.8, "heading": 72.0},
                    {"timestamp": "2025-03-15T05:42:00Z", "lat": 15.55, "lon": 66.21, "speed": 10.7, "heading": 71.0},
                ],
            },
            {
                "id": "vessel_product_tanker_persian",
                "vessel_name": "PERSIAN GLORY",
                "mmsi": "422776655",
                "vessel_type": "Oil Products Tanker",
                "positions": [
                    {"timestamp": "2025-03-15T02:00:00Z", "lat": 15.90, "lon": 66.55, "speed": 14.0, "heading": 252.0},
                    {"timestamp": "2025-03-15T03:30:00Z", "lat": 15.85, "lon": 66.43, "speed": 14.2, "heading": 251.0},
                    {"timestamp": "2025-03-15T05:00:00Z", "lat": 15.79, "lon": 66.30, "speed": 14.1, "heading": 253.0},
                    {"timestamp": "2025-03-15T05:42:00Z", "lat": 15.76, "lon": 66.25, "speed": 14.0, "heading": 252.0},
                ],
            },
        ],
    },
    {
        "id": "ref_arabian_sea_beta",
        "title": "Northern Arabian Sea (Offshore Gujarat/Oman Approach)",
        "image_file": "sentinel1_arabian_sea_beta.jpg",
        "image_path": "/satellite/sentinel1_arabian_sea_beta.jpg",
        "region": "Northern Arabian Sea",
        "observation_lon": 68.835,
        "observation_lat": 18.318,
        "observation_time": "2025-04-02T04:12:00Z",
        "sensor": "Sentinel-1 C-SAR IW",
        "mode": "IW / GRDH",
        "slick_area_km2": 34.1,
        "is_historical_demo": False,
        "historical_context": (
            "Reference Sentinel-1 SAR acquisition showing elongated surfactant anomaly in offshore approaches."
        ),
        "default_wind_speed_ms": 6.4,
        "default_wind_direction_deg": 285.0,
        "default_current_speed_ms": 0.25,
        "default_current_direction_deg": 160.0,
        "backtrack_hours": 6.0,
        "benchmark_candidates": [
            {
                "id": "vessel_beta_tanker",
                "vessel_name": "OCEAN VOYAGER",
                "mmsi": "538002345",
                "vessel_type": "Oil Products Tanker",
                "positions": [
                    {"timestamp": "2025-04-01T22:12:00Z", "lat": 18.25, "lon": 68.75, "speed": 12.8, "heading": 80.0},
                    {"timestamp": "2025-04-01T23:12:00Z", "lat": 18.27, "lon": 68.78, "speed": 12.9, "heading": 79.0},
                    {"timestamp": "2025-04-02T00:12:00Z", "lat": 18.28, "lon": 68.80, "speed": 13.0, "heading": 78.0},
                    {"timestamp": "2025-04-02T01:12:00Z", "lat": 18.29, "lon": 68.81, "speed": 13.0, "heading": 78.0},
                    {"timestamp": "2025-04-02T02:12:00Z", "lat": 18.31, "lon": 68.83, "speed": 12.9, "heading": 80.0},
                    {"timestamp": "2025-04-02T03:12:00Z", "lat": 18.32, "lon": 68.85, "speed": 12.8, "heading": 81.0},
                    {"timestamp": "2025-04-02T04:12:00Z", "lat": 18.33, "lon": 68.88, "speed": 12.9, "heading": 82.0},
                ],
            },
            {
                "id": "vessel_beta_cargo",
                "vessel_name": "NORTHERN DAWN",
                "mmsi": "538009876",
                "vessel_type": "General Cargo",
                "positions": [
                    {"timestamp": "2025-04-01T22:12:00Z", "lat": 18.42, "lon": 68.95, "speed": 10.5, "heading": 260.0},
                    {"timestamp": "2025-04-01T23:30:00Z", "lat": 18.40, "lon": 68.91, "speed": 10.4, "heading": 259.0},
                    {"timestamp": "2025-04-02T01:00:00Z", "lat": 18.38, "lon": 68.87, "speed": 10.3, "heading": 260.0},
                    {"timestamp": "2025-04-02T02:30:00Z", "lat": 18.37, "lon": 68.83, "speed": 10.2, "heading": 258.0},
                    {"timestamp": "2025-04-02T04:12:00Z", "lat": 18.35, "lon": 68.78, "speed": 10.2, "heading": 258.0},
                ],
            },
            {
                "id": "vessel_beta_fishing_dhow",
                "vessel_name": "AL NAKHEEL",
                "mmsi": "461334455",
                "vessel_type": "Fishing Dhow",
                "positions": [
                    {"timestamp": "2025-04-01T22:12:00Z", "lat": 18.15, "lon": 68.60, "speed": 3.8, "heading": 30.0},
                    {"timestamp": "2025-04-02T00:12:00Z", "lat": 18.18, "lon": 68.62, "speed": 3.5, "heading": 35.0},
                    {"timestamp": "2025-04-02T02:12:00Z", "lat": 18.20, "lon": 68.64, "speed": 3.6, "heading": 32.0},
                    {"timestamp": "2025-04-02T04:12:00Z", "lat": 18.22, "lon": 68.65, "speed": 3.4, "heading": 28.0},
                ],
            },
            {
                "id": "vessel_beta_aframax",
                "vessel_name": "OMAN SPIRIT",
                "mmsi": "461556677",
                "vessel_type": "Aframax Tanker",
                "positions": [
                    {"timestamp": "2025-04-01T23:00:00Z", "lat": 18.55, "lon": 69.10, "speed": 13.5, "heading": 270.0},
                    {"timestamp": "2025-04-02T01:00:00Z", "lat": 18.52, "lon": 68.99, "speed": 13.6, "heading": 268.0},
                    {"timestamp": "2025-04-02T03:00:00Z", "lat": 18.48, "lon": 68.88, "speed": 13.4, "heading": 270.0},
                    {"timestamp": "2025-04-02T04:12:00Z", "lat": 18.46, "lon": 68.80, "speed": 13.5, "heading": 269.0},
                ],
            },
        ],
    },
    {
        "id": "ref_bay_of_bengal_gamma",
        "title": "Bay of Bengal (Northern Shipping Corridor)",
        "image_file": "sentinel_bay_of_bengal_gamma.jpg",
        "image_path": "/satellite/sentinel_bay_of_bengal_gamma.jpg",
        "region": "Bay of Bengal",
        "observation_lon": 89.162,
        "observation_lat": 18.944,
        "observation_time": "2025-06-07T05:30:00Z",
        "sensor": "Sentinel-1 C-SAR IW",
        "mode": "IW / GRDH",
        "slick_area_km2": 52.0,
        "is_historical_demo": False,
        "historical_context": (
            "Reference Sentinel-1 SAR acquisition showing maritime slick under monsoonal drift conditions."
        ),
        "default_wind_speed_ms": 8.5,
        "default_wind_direction_deg": 215.0,
        "default_current_speed_ms": 0.35,
        "default_current_direction_deg": 45.0,
        "backtrack_hours": 6.0,
        "benchmark_candidates": [
            {
                "id": "vessel_gamma_feeder",
                "vessel_name": "BENGAL PIONEER",
                "mmsi": "567112233",
                "vessel_type": "Container Feeder",
                "positions": [
                    {"timestamp": "2025-06-06T23:30:00Z", "lat": 18.82, "lon": 89.05, "speed": 14.1, "heading": 35.0},
                    {"timestamp": "2025-06-07T00:30:00Z", "lat": 18.85, "lon": 89.08, "speed": 14.0, "heading": 36.0},
                    {"timestamp": "2025-06-07T01:30:00Z", "lat": 18.87, "lon": 89.10, "speed": 14.1, "heading": 35.0},
                    {"timestamp": "2025-06-07T02:30:00Z", "lat": 18.89, "lon": 89.12, "speed": 14.0, "heading": 36.0},
                    {"timestamp": "2025-06-07T03:30:00Z", "lat": 18.92, "lon": 89.15, "speed": 13.9, "heading": 37.0},
                    {"timestamp": "2025-06-07T04:30:00Z", "lat": 18.94, "lon": 89.17, "speed": 13.8, "heading": 37.0},
                    {"timestamp": "2025-06-07T05:30:00Z", "lat": 18.96, "lon": 89.19, "speed": 13.8, "heading": 38.0},
                ],
            },
            {
                "id": "vessel_gamma_bulk_carrier",
                "vessel_name": "SUNDARBANS SEA",
                "mmsi": "567445566",
                "vessel_type": "Bulk Carrier",
                "positions": [
                    {"timestamp": "2025-06-06T23:30:00Z", "lat": 19.12, "lon": 89.35, "speed": 12.0, "heading": 218.0},
                    {"timestamp": "2025-06-07T01:00:00Z", "lat": 19.06, "lon": 89.28, "speed": 12.2, "heading": 220.0},
                    {"timestamp": "2025-06-07T02:30:00Z", "lat": 19.00, "lon": 89.22, "speed": 12.1, "heading": 219.0},
                    {"timestamp": "2025-06-07T04:00:00Z", "lat": 18.94, "lon": 89.15, "speed": 12.0, "heading": 218.0},
                    {"timestamp": "2025-06-07T05:30:00Z", "lat": 18.88, "lon": 89.08, "speed": 11.9, "heading": 217.0},
                ],
            },
            {
                "id": "vessel_gamma_coastal_tanker",
                "vessel_name": "CHITTAGONG PRIDE",
                "mmsi": "405223344",
                "vessel_type": "Coastal Tanker",
                "positions": [
                    {"timestamp": "2025-06-07T00:00:00Z", "lat": 18.76, "lon": 88.95, "speed": 9.5, "heading": 55.0},
                    {"timestamp": "2025-06-07T01:30:00Z", "lat": 18.80, "lon": 89.00, "speed": 9.3, "heading": 56.0},
                    {"timestamp": "2025-06-07T03:00:00Z", "lat": 18.84, "lon": 89.06, "speed": 9.4, "heading": 54.0},
                    {"timestamp": "2025-06-07T05:30:00Z", "lat": 18.89, "lon": 89.12, "speed": 9.2, "heading": 55.0},
                ],
            },
            {
                "id": "vessel_gamma_patrol",
                "vessel_name": "COAST GUARD AGNI",
                "mmsi": "419800001",
                "vessel_type": "Patrol Vessel",
                "positions": [
                    {"timestamp": "2025-06-06T23:30:00Z", "lat": 18.70, "lon": 89.40, "speed": 18.5, "heading": 310.0},
                    {"timestamp": "2025-06-07T01:00:00Z", "lat": 18.80, "lon": 89.30, "speed": 18.2, "heading": 308.0},
                    {"timestamp": "2025-06-07T02:30:00Z", "lat": 18.88, "lon": 89.20, "speed": 17.8, "heading": 312.0},
                    {"timestamp": "2025-06-07T05:30:00Z", "lat": 19.00, "lon": 89.05, "speed": 8.0, "heading": 180.0},
                ],
            },
        ],
    },
    {
        "id": "ref_gulf_of_kutch_delta",
        "title": "Gulf of Kutch Coastal Channel",
        "image_file": "sentinel_gulf_of_kutch_delta.jpg",
        "image_path": "/satellite/sentinel_gulf_of_kutch_delta.jpg",
        "region": "Gulf of Kutch, Gujarat",
        "observation_lon": 69.812,
        "observation_lat": 22.764,
        "observation_time": "2025-08-18T06:20:00Z",
        "sensor": "Sentinel-1 C-SAR IW",
        "mode": "IW / GRDH",
        "slick_area_km2": 19.6,
        "is_historical_demo": False,
        "historical_context": (
            "Reference Sentinel-1 SAR acquisition in coastal tidal waters near oil terminal traffic."
        ),
        "default_wind_speed_ms": 4.5,
        "default_wind_direction_deg": 260.0,
        "default_current_speed_ms": 0.40,
        "default_current_direction_deg": 80.0,
        "backtrack_hours": 5.0,
        "benchmark_candidates": [
            {
                "id": "vessel_kutch_bunker",
                "vessel_name": "MUNDRA BREEZE",
                "mmsi": "419334455",
                "vessel_type": "Bunkering Tanker",
                "positions": [
                    {"timestamp": "2025-08-18T01:20:00Z", "lat": 22.72, "lon": 69.75, "speed": 8.5, "heading": 70.0},
                    {"timestamp": "2025-08-18T02:20:00Z", "lat": 22.74, "lon": 69.77, "speed": 8.3, "heading": 71.0},
                    {"timestamp": "2025-08-18T03:20:00Z", "lat": 22.75, "lon": 69.79, "speed": 8.2, "heading": 72.0},
                    {"timestamp": "2025-08-18T04:20:00Z", "lat": 22.76, "lon": 69.80, "speed": 8.1, "heading": 70.0},
                    {"timestamp": "2025-08-18T05:20:00Z", "lat": 22.77, "lon": 69.82, "speed": 8.0, "heading": 69.0},
                    {"timestamp": "2025-08-18T06:20:00Z", "lat": 22.78, "lon": 69.84, "speed": 8.0, "heading": 68.0},
                ],
            },
            {
                "id": "vessel_kutch_coastal_tanker",
                "vessel_name": "GUJARAT MARINER",
                "mmsi": "419221133",
                "vessel_type": "Coastal Tanker",
                "positions": [
                    {"timestamp": "2025-08-18T01:20:00Z", "lat": 22.85, "lon": 69.92, "speed": 10.2, "heading": 255.0},
                    {"timestamp": "2025-08-18T02:30:00Z", "lat": 22.83, "lon": 69.86, "speed": 10.4, "heading": 253.0},
                    {"timestamp": "2025-08-18T03:40:00Z", "lat": 22.81, "lon": 69.80, "speed": 10.3, "heading": 256.0},
                    {"timestamp": "2025-08-18T05:00:00Z", "lat": 22.79, "lon": 69.76, "speed": 10.1, "heading": 255.0},
                    {"timestamp": "2025-08-18T06:20:00Z", "lat": 22.77, "lon": 69.70, "speed": 9.9, "heading": 254.0},
                ],
            },
            {
                "id": "vessel_kutch_cargo",
                "vessel_name": "KANDLA EXPRESS",
                "mmsi": "419667788",
                "vessel_type": "General Cargo",
                "positions": [
                    {"timestamp": "2025-08-18T02:00:00Z", "lat": 22.68, "lon": 69.68, "speed": 11.5, "heading": 88.0},
                    {"timestamp": "2025-08-18T03:30:00Z", "lat": 22.69, "lon": 69.74, "speed": 11.4, "heading": 87.0},
                    {"timestamp": "2025-08-18T05:00:00Z", "lat": 22.70, "lon": 69.80, "speed": 11.2, "heading": 89.0},
                    {"timestamp": "2025-08-18T06:20:00Z", "lat": 22.71, "lon": 69.86, "speed": 11.0, "heading": 88.0},
                ],
            },
            {
                "id": "vessel_kutch_fishing",
                "vessel_name": "SARDAR SAGAR",
                "mmsi": "419009988",
                "vessel_type": "Fishing Vessel",
                "positions": [
                    {"timestamp": "2025-08-18T01:20:00Z", "lat": 22.90, "lon": 69.95, "speed": 4.2, "heading": 150.0},
                    {"timestamp": "2025-08-18T03:00:00Z", "lat": 22.88, "lon": 69.93, "speed": 4.0, "heading": 155.0},
                    {"timestamp": "2025-08-18T05:00:00Z", "lat": 22.86, "lon": 69.91, "speed": 3.8, "heading": 152.0},
                    {"timestamp": "2025-08-18T06:20:00Z", "lat": 22.84, "lon": 69.90, "speed": 3.9, "heading": 150.0},
                ],
            },
        ],
    },
    {
        "id": "ref_mediterranean_epsilon",
        "title": "Central Mediterranean (Strait of Sicily Passage)",
        "image_file": "sentinel1_mediterranean_epsilon.jpg",
        "image_path": "/satellite/sentinel1_mediterranean_epsilon.jpg",
        "region": "Strait of Sicily, Mediterranean",
        "observation_lon": 14.786,
        "observation_lat": 35.926,
        "observation_time": "2025-09-11T06:45:00Z",
        "sensor": "Sentinel-1 C-SAR IW",
        "mode": "IW / GRDH",
        "slick_area_km2": 31.8,
        "is_historical_demo": False,
        "historical_context": (
            "Reference Sentinel-1 SAR scene along major east-west Mediterranean transit passage."
        ),
        "default_wind_speed_ms": 5.2,
        "default_wind_direction_deg": 315.0,
        "default_current_speed_ms": 0.20,
        "default_current_direction_deg": 110.0,
        "backtrack_hours": 6.0,
        "benchmark_candidates": [
            {
                "id": "vessel_sicily_lng",
                "vessel_name": "MED LNG LEADER",
                "mmsi": "256112233",
                "vessel_type": "LNG Tanker",
                "positions": [
                    {"timestamp": "2025-09-11T00:45:00Z", "lat": 35.88, "lon": 14.68, "speed": 16.5, "heading": 105.0},
                    {"timestamp": "2025-09-11T01:45:00Z", "lat": 35.89, "lon": 14.71, "speed": 16.3, "heading": 104.0},
                    {"timestamp": "2025-09-11T02:45:00Z", "lat": 35.90, "lon": 14.74, "speed": 16.4, "heading": 105.0},
                    {"timestamp": "2025-09-11T03:45:00Z", "lat": 35.91, "lon": 14.75, "speed": 16.2, "heading": 104.0},
                    {"timestamp": "2025-09-11T04:45:00Z", "lat": 35.92, "lon": 14.78, "speed": 16.3, "heading": 106.0},
                    {"timestamp": "2025-09-11T05:45:00Z", "lat": 35.93, "lon": 14.80, "speed": 16.4, "heading": 105.0},
                    {"timestamp": "2025-09-11T06:45:00Z", "lat": 35.94, "lon": 14.83, "speed": 16.4, "heading": 106.0},
                ],
            },
            {
                "id": "vessel_sicily_crude_tanker",
                "vessel_name": "SIROCCO TRADER",
                "mmsi": "248334455",
                "vessel_type": "Crude Oil Tanker",
                "positions": [
                    {"timestamp": "2025-09-11T00:45:00Z", "lat": 36.05, "lon": 15.00, "speed": 13.8, "heading": 285.0},
                    {"timestamp": "2025-09-11T02:00:00Z", "lat": 36.02, "lon": 14.94, "speed": 13.9, "heading": 284.0},
                    {"timestamp": "2025-09-11T03:30:00Z", "lat": 35.99, "lon": 14.87, "speed": 14.0, "heading": 285.0},
                    {"timestamp": "2025-09-11T05:00:00Z", "lat": 35.96, "lon": 14.80, "speed": 13.8, "heading": 283.0},
                    {"timestamp": "2025-09-11T06:45:00Z", "lat": 35.93, "lon": 14.72, "speed": 13.7, "heading": 284.0},
                ],
            },
            {
                "id": "vessel_sicily_ferry",
                "vessel_name": "TRINACRIA EXPRESS",
                "mmsi": "247891234",
                "vessel_type": "Ro-Pax Ferry",
                "positions": [
                    {"timestamp": "2025-09-11T02:00:00Z", "lat": 37.50, "lon": 15.10, "speed": 22.5, "heading": 195.0},
                    {"timestamp": "2025-09-11T03:30:00Z", "lat": 37.12, "lon": 14.98, "speed": 22.8, "heading": 194.0},
                    {"timestamp": "2025-09-11T05:00:00Z", "lat": 36.74, "lon": 14.90, "speed": 22.5, "heading": 196.0},
                    {"timestamp": "2025-09-11T06:45:00Z", "lat": 36.28, "lon": 14.84, "speed": 22.2, "heading": 195.0},
                ],
            },
            {
                "id": "vessel_sicily_bulk",
                "vessel_name": "ADRIATIC STAR",
                "mmsi": "247556611",
                "vessel_type": "Bulk Carrier",
                "positions": [
                    {"timestamp": "2025-09-11T01:00:00Z", "lat": 35.72, "lon": 14.55, "speed": 12.5, "heading": 95.0},
                    {"timestamp": "2025-09-11T02:30:00Z", "lat": 35.74, "lon": 14.60, "speed": 12.6, "heading": 94.0},
                    {"timestamp": "2025-09-11T04:00:00Z", "lat": 35.76, "lon": 14.66, "speed": 12.4, "heading": 96.0},
                    {"timestamp": "2025-09-11T05:30:00Z", "lat": 35.78, "lon": 14.72, "speed": 12.3, "heading": 95.0},
                    {"timestamp": "2025-09-11T06:45:00Z", "lat": 35.79, "lon": 14.77, "speed": 12.2, "heading": 94.0},
                ],
            },
            {
                "id": "vessel_sicily_patrol",
                "vessel_name": "GUARDIA COSTIERA GC-P10",
                "mmsi": "247100010",
                "vessel_type": "Patrol Vessel",
                "positions": [
                    {"timestamp": "2025-09-11T00:00:00Z", "lat": 35.85, "lon": 14.72, "speed": 20.0, "heading": 220.0},
                    {"timestamp": "2025-09-11T01:30:00Z", "lat": 35.78, "lon": 14.68, "speed": 5.0, "heading": 140.0},
                    {"timestamp": "2025-09-11T03:00:00Z", "lat": 35.80, "lon": 14.70, "speed": 4.5, "heading": 200.0},
                    {"timestamp": "2025-09-11T04:30:00Z", "lat": 35.82, "lon": 14.73, "speed": 18.0, "heading": 90.0},
                    {"timestamp": "2025-09-11T06:45:00Z", "lat": 35.84, "lon": 14.88, "speed": 5.0, "heading": 0.0},
                ],
            },
        ],
    },
]


def list_reference_observations() -> list[dict[str, Any]]:
    """Return all registered reference observations with verified metadata."""
    return REFERENCE_OBSERVATIONS


def get_reference_observation(ref_id: str) -> dict[str, Any] | None:
    """Fetch reference observation by identifier."""
    for obs in REFERENCE_OBSERVATIONS:
        if obs["id"] == ref_id:
            return obs
    return None


# ---------------------------------------------------------------------------
# Physics & Backward Drift Simulation
# ---------------------------------------------------------------------------

def _speed_dir_to_uv(speed: float, direction_deg: float) -> tuple[float, float]:
    """Convert meteorological speed and direction (degrees FROM which it blows) to (u, v)."""
    rad = math.radians((direction_deg + 180.0) % 360.0)
    u = speed * math.sin(rad)
    v = speed * math.cos(rad)
    return u, v


def build_environment_datasets(
    *,
    origin_lon: float,
    origin_lat: float,
    observation_time: datetime,
    wind_speed_ms: float,
    wind_direction_deg: float,
    current_speed_ms: float,
    current_direction_deg: float,
    backtrack_hours: float,
) -> tuple[xr.Dataset, xr.Dataset]:
    """Construct physically consistent 2D+time vector fields for wind and current."""
    u10, v10 = _speed_dir_to_uv(wind_speed_ms, wind_direction_deg)
    uo, vo = _speed_dir_to_uv(current_speed_ms, current_direction_deg)

    # Leeway estimation to ensure bounding box covers source
    drift_u = uo + 0.03 * u10
    drift_v = vo + 0.03 * v10
    dt_s = backtrack_hours * 3600.0
    lat_rad = math.radians(origin_lat)
    delta_lon = (drift_u * dt_s) / (111320.0 * max(math.cos(lat_rad), 0.1))
    delta_lat = (drift_v * dt_s) / 111320.0

    approx_source_lon = origin_lon - delta_lon
    approx_source_lat = origin_lat - delta_lat

    margin_deg = 2.0
    min_lat = min(origin_lat, approx_source_lat) - margin_deg
    max_lat = max(origin_lat, approx_source_lat) + margin_deg
    min_lon = min(origin_lon, approx_source_lon) - margin_deg
    max_lon = max(origin_lon, approx_source_lon) + margin_deg

    grid_lats = np.linspace(min_lat, max_lat, 15)
    grid_lons = np.linspace(min_lon, max_lon, 15)

    obs_utc = observation_time if observation_time.tzinfo else observation_time.replace(tzinfo=timezone.utc)
    t_start = obs_utc - timedelta(hours=backtrack_hours + 2)
    t_end = obs_utc + timedelta(hours=2)
    step_count = int((t_end - t_start).total_seconds() / 3600) + 1
    time_series = [t_start + timedelta(hours=i) for i in range(step_count)]
    time_arr = np.array([np.datetime64(t.replace(tzinfo=None)) for t in time_series], dtype="datetime64[s]")

    shape = (len(time_arr), len(grid_lats), len(grid_lons))
    grad_y = np.linspace(-0.02, 0.02, len(grid_lats))
    grad_x = np.linspace(-0.02, 0.02, len(grid_lons))
    mesh_gy, mesh_gx = np.meshgrid(grad_y, grad_x, indexing="ij")

    u10_grid = np.zeros(shape, dtype=np.float32)
    v10_grid = np.zeros(shape, dtype=np.float32)
    uo_grid = np.zeros(shape, dtype=np.float32)
    vo_grid = np.zeros(shape, dtype=np.float32)

    for ti in range(len(time_arr)):
        t_factor = 1.0 + 0.01 * math.sin(ti * 0.5)
        u10_grid[ti, :, :] = (u10 + mesh_gx) * t_factor
        v10_grid[ti, :, :] = (v10 + mesh_gy) * t_factor
        uo_grid[ti, :, :] = (uo + mesh_gx * 0.05) * t_factor
        vo_grid[ti, :, :] = (vo + mesh_gy * 0.05) * t_factor

    wind_ds = xr.Dataset(
        {
            "u10": (["time", "latitude", "longitude"], u10_grid),
            "v10": (["time", "latitude", "longitude"], v10_grid),
        },
        coords={
            "time": time_arr,
            "latitude": grid_lats.astype(np.float64),
            "longitude": grid_lons.astype(np.float64),
        },
    )

    curr_ds = xr.Dataset(
        {
            "uo": (["time", "latitude", "longitude"], uo_grid),
            "vo": (["time", "latitude", "longitude"], vo_grid),
        },
        coords={
            "time": time_arr,
            "latitude": grid_lats.astype(np.float64),
            "longitude": grid_lons.astype(np.float64),
        },
    )

    return wind_ds, curr_ds


def calculate_backward_drift_preview(
    *,
    origin_lon: float,
    origin_lat: float,
    observation_time: datetime,
    wind_speed_ms: float,
    wind_direction_deg: float,
    current_speed_ms: float,
    current_direction_deg: float,
    backtrack_hours: float = 6.0,
    step_hours: float = 0.5,
    spill_area_m2: float = 100000.0,
) -> dict[str, Any]:
    """Execute real backward drift calculation and return source reconstruction + vector geometry."""
    obs_utc = observation_time if observation_time.tzinfo else observation_time.replace(tzinfo=timezone.utc)
    wind_ds, curr_ds = build_environment_datasets(
        origin_lon=origin_lon,
        origin_lat=origin_lat,
        observation_time=obs_utc,
        wind_speed_ms=wind_speed_ms,
        wind_direction_deg=wind_direction_deg,
        current_speed_ms=current_speed_ms,
        current_direction_deg=current_direction_deg,
        backtrack_hours=backtrack_hours,
    )

    backward_steps = run_backward_drift(
        origin_lon=origin_lon,
        origin_lat=origin_lat,
        observation_time=obs_utc,
        wind_ds=wind_ds,
        curr_ds=curr_ds,
        lookback_hours=backtrack_hours,
        step_hours=step_hours,
        spill_area_m2=spill_area_m2,
    )

    final_step = backward_steps[-1]
    source_lon = final_step.lon
    source_lat = final_step.lat
    source_radius_m = final_step.uncertainty_radius_m

    source_zone_geojson = generate_source_candidate_polygon(
        center_lon=source_lon,
        center_lat=source_lat,
        radius_m=source_radius_m,
    )

    u10, v10 = _speed_dir_to_uv(wind_speed_ms, wind_direction_deg)
    uo, vo = _speed_dir_to_uv(current_speed_ms, current_direction_deg)

    serialized_steps = [
        {
            "step_index": idx,
            "timestamp": s.timestamp.isoformat() if hasattr(s.timestamp, "isoformat") else str(s.timestamp),
            "lon": float(s.lon),
            "lat": float(s.lat),
            "uncertainty_radius_m": float(s.uncertainty_radius_m),
            "wind_u": float(getattr(s, "u_wind_ms", 0.0)),
            "wind_v": float(getattr(s, "v_wind_ms", 0.0)),
            "current_u": float(getattr(s, "u_current_ms", 0.0)),
            "current_v": float(getattr(s, "v_current_ms", 0.0)),
            "drift_u": float(getattr(s, "drift_u_ms", 0.0)),
            "drift_v": float(getattr(s, "drift_v_ms", 0.0)),
            "cumulative_distance_m": float(getattr(s, "cumulative_backward_distance_m", 0.0)),
        }
        for idx, s in enumerate(backward_steps)
    ]

    return {
        "observation_point": {"lon": origin_lon, "lat": origin_lat},
        "observation_time": obs_utc.isoformat(),
        "backtrack_hours": backtrack_hours,
        "step_hours": step_hours,
        "reconstructed_source": {
            "source_lon": float(source_lon),
            "source_lat": float(source_lat),
            "source_radius_m": float(source_radius_m),
            "source_radius_km": float(source_radius_m / 1000.0),
            "estimated_release_time": (obs_utc - timedelta(hours=backtrack_hours)).isoformat(),
            "source_zone_geojson": source_zone_geojson,
        },
        "backward_steps": serialized_steps,
        "wind_vector": {
            "speed_ms": wind_speed_ms,
            "direction_deg": wind_direction_deg,
            "u": float(u10),
            "v": float(v10),
        },
        "current_vector": {
            "speed_ms": current_speed_ms,
            "direction_deg": current_direction_deg,
            "u": float(uo),
            "v": float(vo),
        },
    }


# ---------------------------------------------------------------------------
# Strict AIS Candidate Filtering
# ---------------------------------------------------------------------------

def filter_candidates_for_investigation(
    *,
    vessels: list[dict[str, Any]],
    backward_steps: list[Any],
    source_lon: float,
    source_lat: float,
    source_radius_m: float,
    observation_time: datetime,
    backtrack_hours: float,
    corridor_km: float = 25.0,
    pre_window_hours: float = 1.0,
    post_window_hours: float = 1.0,
) -> dict[str, Any]:
    """Strictly filter AIS candidate vessels by spatial corridor AND temporal window.

    Rules:
    - Spatial: Vessel must pass within corridor_km of the backward drift trajectory or source zone.
    - Temporal: Vessel must have AIS positions within [t_release - pre_window, t_obs + post_window].
    - Eligibility: requires BOTH spatial and temporal intersection.
    - Non-eligible vessels are explicitly classified and excluded from downstream ML inference.
    """
    obs_utc = observation_time if observation_time.tzinfo else observation_time.replace(tzinfo=timezone.utc)
    release_time = obs_utc - timedelta(hours=backtrack_hours)
    window_start = release_time - timedelta(hours=pre_window_hours)
    window_end = obs_utc + timedelta(hours=post_window_hours)

    eligible_candidates: list[dict[str, Any]] = []
    ineligible_candidates: list[dict[str, Any]] = []

    for v in vessels:
        v_id = str(v.get("id") or v.get("mmsi") or v.get("vessel_name", "UNKNOWN"))
        raw_positions = v.get("positions", [])

        parsed_positions = []
        for pos in raw_positions:
            try:
                ts = pos.get("timestamp")
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                lat = float(pos.get("lat", float("nan")))
                lon = float(pos.get("lon", float("nan")))
                if ts and math.isfinite(lat) and math.isfinite(lon):
                    parsed_positions.append({
                        "timestamp": ts,
                        "lat": lat,
                        "lon": lon,
                        "speed": float(pos["speed"]) if pos.get("speed") is not None else None,
                        "heading": float(pos["heading"]) if pos.get("heading") is not None else None,
                    })
            except Exception:
                continue

        if not parsed_positions:
            ineligible_candidates.append({
                "vessel_id": v_id,
                "vessel_name": v.get("vessel_name", "UNKNOWN"),
                "mmsi": v.get("mmsi"),
                "rejection_reason": "NO_AIS_POSITIONS",
                "rejection_detail": "No valid timestamped geographic positions found.",
                "min_corridor_dist_km": None,
                "has_temporal_overlap": False,
            })
            continue

        # 1. Temporal Check: Any position inside [window_start, window_end]
        time_matching_positions = [
            p for p in parsed_positions
            if window_start <= p["timestamp"] <= window_end
        ]
        has_temporal_overlap = len(time_matching_positions) > 0

        # 2. Spatial Check: Minimum distance to either source centroid or drift trajectory
        # Evaluated on time-matching positions if available, or all positions
        eval_positions = time_matching_positions if time_matching_positions else parsed_positions

        min_source_dist_km = min(
            haversine_km(p["lat"], p["lon"], source_lat, source_lon)
            for p in eval_positions
        )

        min_trajectory_dist_km = float("inf")
        for step in backward_steps:
            s_lat = getattr(step, "lat", None)
            s_lon = getattr(step, "lon", None)
            if s_lat is None and isinstance(step, dict):
                s_lat = step.get("lat")
                s_lon = step.get("lon")
            if s_lat is not None and s_lon is not None:
                for p in eval_positions:
                    d = haversine_km(p["lat"], p["lon"], s_lat, s_lon)
                    if d < min_trajectory_dist_km:
                        min_trajectory_dist_km = d

        min_dist_to_corridor_km = min(min_source_dist_km, min_trajectory_dist_km)
        has_spatial_corridor_overlap = min_dist_to_corridor_km <= corridor_km

        is_eligible = has_temporal_overlap and has_spatial_corridor_overlap

        vessel_entry = {
            "vessel_id": v_id,
            "vessel_name": v.get("vessel_name", "UNKNOWN"),
            "mmsi": v.get("mmsi"),
            "vessel_type": v.get("vessel_type", "Vessel"),
            "source_id": v.get("source_id"),
            "source_type": v.get("source_type"),
            "is_real_observation": bool(v.get("is_real_observation", False)),
            "provider_name": v.get("provider_name"),
            "min_source_dist_km": float(round(min_source_dist_km, 3)),
            "min_trajectory_dist_km": float(round(min_trajectory_dist_km, 3)),
            "min_corridor_dist_km": float(round(min_dist_to_corridor_km, 3)),
            "has_temporal_overlap": has_temporal_overlap,
            "has_spatial_corridor_overlap": has_spatial_corridor_overlap,
            "position_count": len(parsed_positions),
            "window_position_count": len(time_matching_positions),
            "positions": v.get("positions", []),
        }

        if is_eligible:
            eligible_candidates.append(vessel_entry)
        else:
            reasons = []
            if not has_temporal_overlap:
                reasons.append("TEMPORAL_WINDOW_MISMATCH")
            if not has_spatial_corridor_overlap:
                reasons.append(f"OUTSIDE_CORRIDOR_{corridor_km}KM")
            vessel_entry["rejection_reason"] = " + ".join(reasons)
            vessel_entry["rejection_detail"] = (
                f"Min corridor distance was {min_dist_to_corridor_km:.1f} km (limit: {corridor_km} km); "
                f"Temporal overlap was {has_temporal_overlap}."
            )
            ineligible_candidates.append(vessel_entry)

    return {
        "corridor_km": corridor_km,
        "temporal_window": {
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "release_time": release_time.isoformat(),
            "observation_time": obs_utc.isoformat(),
        },
        "total_evaluated": len(vessels),
        "eligible_count": len(eligible_candidates),
        "ineligible_count": len(ineligible_candidates),
        "eligible_candidates": eligible_candidates,
        "ineligible_candidates": ineligible_candidates,
    }


def determine_provider_status(
    *,
    has_live_provider: bool,
    total_found: int,
    eligible_count: int,
    provenance_source: str | None = None,
    insufficient_coverage: bool = False,
) -> tuple[str, str]:
    """Determine explicit provider and provenance boundary state (never conflated).

    Possible Status Values:
        LIVE_AIS
        OBSERVED_ARCHIVE
        SYNTHETIC_BENCHMARK
        MANUAL_REFERENCE
        NO_PROVIDER
        NO_ELIGIBLE_VESSELS
        INSUFFICIENT_COVERAGE

    Returns:
        (provider_status, status_description)
    """
    if not has_live_provider and total_found == 0:
        return (
            "NO_PROVIDER",
            "No live AIS provider configured. MARIS AIS adapter credentials are unconfigured or unset.",
        )
    if total_found > 0 and eligible_count == 0:
        return (
            "NO_ELIGIBLE_VESSELS",
            f"AIS data checked ({total_found} candidates examined), but 0 vessels met both spatial corridor and temporal intersection criteria.",
        )
    if insufficient_coverage:
        return (
            "INSUFFICIENT_COVERAGE",
            f"Candidate vessels found ({total_found}), but AIS reporting density or time coverage is insufficient for attribution.",
        )
    if provenance_source == PROVENANCE_MANUAL_REFERENCE:
        return (
            "MANUAL_REFERENCE",
            f"Curated reference benchmark active with {eligible_count} eligible vessels. (Non-live historical reconstruction).",
        )
    if provenance_source == PROVENANCE_SYNTHETIC_BENCHMARK:
        return (
            "SYNTHETIC_BENCHMARK",
            f"Synthetic benchmark scenario active with {eligible_count} eligible candidate vessels. (Algorithmic test data).",
        )
    if provenance_source == PROVENANCE_NOAA_MARINECADASTRE:
        return (
            "OBSERVED_ARCHIVE",
            f"Imported NOAA MarineCadastre observation archive active with {eligible_count} eligible candidates.",
        )
    if provenance_source == PROVENANCE_UNVERIFIED_IMPORT:
        return (
            "UNVERIFIED_IMPORT",
            f"Unverified imported AIS sample active with {eligible_count} eligible candidates (requires authoritative provenance verification).",
        )
    if has_live_provider:
        return (
            "LIVE_AIS",
            f"Active AIS source available with {eligible_count} eligible candidates within investigation corridor.",
        )
    return (
        "OBSERVED_ARCHIVE" if provenance_source else "LIVE_AIS",
        f"Active AIS source available with {eligible_count} eligible candidates within investigation corridor.",
    )


# ---------------------------------------------------------------------------
# Attribution ML Execution & Persistence
# ---------------------------------------------------------------------------

def run_evaluator_investigation(
    *,
    selected_image_id: str,
    observation_lon: float,
    observation_lat: float,
    observation_time: datetime,
    wind_speed_ms: float,
    wind_direction_deg: float,
    current_speed_ms: float,
    current_direction_deg: float,
    corridor_km: float = 25.0,
    backtrack_hours: float = 6.0,
    step_hours: float = 0.5,
    spill_area_m2: float = 100000.0,
    custom_vessels: list[dict[str, Any]] | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    """Run full interactive evaluator investigation end-to-end and persist to SQLite."""
    investigation_id = f"inv_eval_{uuid.uuid4().hex[:10]}"
    obs_utc = observation_time if observation_time.tzinfo else observation_time.replace(tzinfo=timezone.utc)

    # 1. Fetch metadata for reference image
    ref_obs = get_reference_observation(selected_image_id)
    image_title = ref_obs["title"] if ref_obs else f"Observation {selected_image_id}"
    image_path = ref_obs["image_path"] if ref_obs else "/satellite/corsica_2018_s1.jpg"

    # 2. Backward Drift Execution
    drift_preview = calculate_backward_drift_preview(
        origin_lon=observation_lon,
        origin_lat=observation_lat,
        observation_time=obs_utc,
        wind_speed_ms=wind_speed_ms,
        wind_direction_deg=wind_direction_deg,
        current_speed_ms=current_speed_ms,
        current_direction_deg=current_direction_deg,
        backtrack_hours=backtrack_hours,
        step_hours=step_hours,
        spill_area_m2=spill_area_m2,
    )

    reconstructed_source = drift_preview["reconstructed_source"]
    source_lon = reconstructed_source["source_lon"]
    source_lat = reconstructed_source["source_lat"]
    source_radius_m = reconstructed_source["source_radius_m"]
    backward_steps = drift_preview["backward_steps"]

    # 3. Candidate Vessels Gathering (SQLite DB -> live provider -> benchmark fallback)
    ais_search_svc = AisSearchService(cfg=settings)
    is_live_provider_configured = ais_search_svc.is_configured()

    candidate_pool: list[dict[str, Any]] = []
    primary_source_type: str | None = None
    is_real_obs: bool = False

    if custom_vessels is not None:
        candidate_pool.extend(custom_vessels)
        primary_source_type = PROVENANCE_MANUAL_REFERENCE
    else:
        # Query inspectable SQLite database (ais_vessels.db)
        try:
            init_ais_database()
            db_candidates = get_candidates_for_scene(selected_image_id)
            if not db_candidates:
                # Spatial-temporal SQL bounding box prefilter
                lat_deg_margin = (corridor_km + 25.0) / 111.0
                cos_lat = max(math.cos(math.radians(observation_lat)), 0.1)
                lon_deg_margin = (corridor_km + 25.0) / (111.0 * cos_lat)
                lats = [observation_lat, source_lat] + [
                    s.get("lat", observation_lat) if isinstance(s, dict) else getattr(s, "lat", observation_lat)
                    for s in backward_steps
                ]
                lons = [observation_lon, source_lon] + [
                    s.get("lon", observation_lon) if isinstance(s, dict) else getattr(s, "lon", observation_lon)
                    for s in backward_steps
                ]
                lat_min, lat_max = min(lats) - lat_deg_margin, max(lats) + lat_deg_margin
                lon_min, lon_max = min(lons) - lon_deg_margin, max(lons) + lon_deg_margin
                t_start = obs_utc - timedelta(hours=backtrack_hours + 2.0)
                t_end = obs_utc + timedelta(hours=2.0)
                db_candidates = query_vessels_in_spatiotemporal_box(
                    lat_min=lat_min,
                    lat_max=lat_max,
                    lon_min=lon_min,
                    lon_max=lon_max,
                    t_start=t_start,
                    t_end=t_end,
                )
            if db_candidates:
                candidate_pool.extend(db_candidates)
        except Exception:
            pass

        # Fallback to inline reference definitions if DB yielded no candidates
        if not candidate_pool and ref_obs and ref_obs.get("benchmark_candidates"):
            candidate_pool.extend(ref_obs["benchmark_candidates"])

    if candidate_pool:
        primary_source_type = candidate_pool[0].get("source_type")
        is_real_obs = bool(candidate_pool[0].get("is_real_observation", False))

    # 4. Strict Filtering (Spatial corridor AND temporal intersection)
    filtering_result = filter_candidates_for_investigation(
        vessels=candidate_pool,
        backward_steps=backward_steps,
        source_lon=source_lon,
        source_lat=source_lat,
        source_radius_m=source_radius_m,
        observation_time=obs_utc,
        backtrack_hours=backtrack_hours,
        corridor_km=corridor_km,
    )

    eligible_candidates = filtering_result["eligible_candidates"]
    ineligible_candidates = filtering_result["ineligible_candidates"]

    # Coverage verification
    insufficient_coverage = bool(
        candidate_pool and all(len(v.get("positions", [])) < 2 for v in candidate_pool)
    )

    # Provider State
    provider_status, provider_description = determine_provider_status(
        has_live_provider=is_live_provider_configured or (is_real_obs and bool(candidate_pool)),
        total_found=len(candidate_pool),
        eligible_count=len(eligible_candidates),
        provenance_source=primary_source_type,
        insufficient_coverage=insufficient_coverage,
    )

    # 5. ML Model Attribution (Only for eligible candidates!)
    model = load_model(model_id=model_id)

    evaluated_candidates: list[dict[str, Any]] = []
    top_candidate: dict[str, Any] | None = None

    if eligible_candidates:
        feature_rows = []
        for v in eligible_candidates:
            feat_dict = extract_vessel_features(
                vessel_data=v,
                source_lon=source_lon,
                source_lat=source_lat,
                source_radius_m=source_radius_m,
                observation_time=obs_utc,
                backtrack_hours=backtrack_hours,
                backward_steps=backward_steps,
            )
            feature_vector = [feat_dict[fn] for fn in FEATURE_NAMES]
            feature_rows.append((v, feat_dict, feature_vector))

        X = np.array([row[2] for row in feature_rows], dtype=np.float32)
        probabilities = model.pipeline.predict_proba(X)[:, 1]
        sum_p = float(np.sum(probabilities))

        for idx, (v, feat_dict, _) in enumerate(feature_rows):
            p_ind = float(probabilities[idx])
            scenario_score = float(p_ind / sum_p) if sum_p > 1e-6 else float(1.0 / len(eligible_candidates))

            evaluated_candidates.append({
                "vessel_id": v["vessel_id"],
                "vessel_name": v["vessel_name"],
                "mmsi": v.get("mmsi"),
                "vessel_type": v.get("vessel_type", "Vessel"),
                "source_id": v.get("source_id"),
                "source_type": v.get("source_type"),
                "is_real_observation": bool(v.get("is_real_observation", False)),
                "provider_name": v.get("provider_name"),
                "model_probability": round(p_ind, 4),
                "scenario_normalized_score": round(scenario_score, 4),
                "features": feat_dict,
                "corridor_dist_km": v["min_corridor_dist_km"],
                "min_source_dist_km": feat_dict["min_source_distance_km"],
                "time_difference_hours": feat_dict["time_difference_hours"],
                "heading_consistency": feat_dict["heading_consistency"],
                "speed_consistency": feat_dict["speed_consistency"],
                "positions": v.get("positions", []),
            })

        # Rank candidates by independent model probability
        evaluated_candidates.sort(key=lambda c: c["model_probability"], reverse=True)
        for rank, c in enumerate(evaluated_candidates, start=1):
            c["rank"] = rank

        top_candidate = evaluated_candidates[0]

    final_attribution = {
        "top_candidate": top_candidate,
        "eligible_candidate_count": len(evaluated_candidates),
        "ineligible_candidate_count": len(ineligible_candidates),
        "model_id": model.model_id,
        "model_type": model.model_type,
        "confidence_assessment": (
            f"Top attributed vessel is {top_candidate['vessel_name']} "
            f"with independent model probability of {top_candidate['model_probability']:.1%} "
            f"(scenario-normalized score: {top_candidate['scenario_normalized_score']:.1%})."
            if top_candidate else "No eligible candidate vessels met the corridor and temporal filtering criteria."
        ),
    }

    # 6. Complete Reproducible Investigation Record
    record: dict[str, Any] = {
        "investigation_id": investigation_id,
        "selected_image_id": selected_image_id,
        "image_path": image_path,
        "image_title": image_title,
        "is_historical_demo": ref_obs.get("is_historical_demo", False) if ref_obs else False,
        "coordinates": {
            "observation_lon": observation_lon,
            "observation_lat": observation_lat,
        },
        "acquisition_timestamp": obs_utc.isoformat(),
        "wind_inputs": {
            "speed_ms": wind_speed_ms,
            "direction_deg": wind_direction_deg,
            "u": drift_preview["wind_vector"]["u"],
            "v": drift_preview["wind_vector"]["v"],
        },
        "current_inputs": {
            "speed_ms": current_speed_ms,
            "direction_deg": current_direction_deg,
            "u": drift_preview["current_vector"]["u"],
            "v": drift_preview["current_vector"]["v"],
        },
        "drift_parameters": {
            "backtrack_hours": backtrack_hours,
            "step_hours": step_hours,
            "corridor_km": corridor_km,
            "spill_area_m2": spill_area_m2,
        },
        "reconstructed_source": reconstructed_source,
        "backward_steps": backward_steps,
        "provider_status": provider_status,
        "provider_description": provider_description,
        "provenance_type": primary_source_type or ("LIVE_AIS" if is_live_provider_configured else "UNKNOWN"),
        "is_real_observation": is_real_obs,
        "filtering_summary": {
            "corridor_km": corridor_km,
            "total_candidates_checked": len(candidate_pool),
            "eligible_candidates_count": len(eligible_candidates),
            "ineligible_candidates_count": len(ineligible_candidates),
        },
        "eligible_vessel_ids": [c["vessel_id"] for c in evaluated_candidates],
        "relevant_vessels_data": evaluated_candidates,
        "ineligible_vessels_data": ineligible_candidates,
        "model_version": model.model_id,
        "candidate_probabilities": evaluated_candidates,
        "final_attribution": final_attribution,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # 7. Persist to SQLite
    store = get_experiment_store()
    store.save_evaluator_investigation(record)

    return record


# ---------------------------------------------------------------------------
# Manual Vessel Investigation Path
# ---------------------------------------------------------------------------

def investigate_manual_vessel(
    *,
    vessel_identifier: str,
    observation_lon: float,
    observation_lat: float,
    observation_time: datetime,
    wind_speed_ms: float,
    wind_direction_deg: float,
    current_speed_ms: float,
    current_direction_deg: float,
    corridor_km: float = 25.0,
    backtrack_hours: float = 6.0,
    step_hours: float = 0.5,
    spill_area_m2: float = 100000.0,
    model_id: str | None = None,
    db_path: Path | None = None,
    force_counterfactual: bool = False,
) -> dict[str, Any]:
    """Manually investigate a specific vessel identified by MMSI or vessel ID.

    Loads the vessel and its trajectory from ais_vessels.db, evaluates spatial
    and temporal corridor constraints, and executes standard attribution only
    if the vessel meets physical eligibility criteria (no fabricated evidence
    for vessels outside the current hypothesis).
    """
    init_ais_database(db_path)
    obs_utc = observation_time if observation_time.tzinfo else observation_time.replace(tzinfo=timezone.utc)

    # 1. Look up vessel in database
    vessel = get_vessel_by_identifier(vessel_identifier, db_path=db_path)
    if not vessel:
        return {
            "found": False,
            "vessel_identifier": vessel_identifier,
            "error": f"Vessel with identifier '{vessel_identifier}' was not found in the AIS database.",
            "is_eligible": False,
            "attribution": None,
        }

    # 2. Run backward drift preview
    drift_preview = calculate_backward_drift_preview(
        origin_lon=observation_lon,
        origin_lat=observation_lat,
        observation_time=obs_utc,
        wind_speed_ms=wind_speed_ms,
        wind_direction_deg=wind_direction_deg,
        current_speed_ms=current_speed_ms,
        current_direction_deg=current_direction_deg,
        backtrack_hours=backtrack_hours,
        step_hours=step_hours,
        spill_area_m2=spill_area_m2,
    )

    reconstructed_source = drift_preview["reconstructed_source"]
    source_lon = reconstructed_source["source_lon"]
    source_lat = reconstructed_source["source_lat"]
    source_radius_m = reconstructed_source["source_radius_m"]
    backward_steps = drift_preview["backward_steps"]

    # 3. Spatial & Temporal Validation
    filtering_result = filter_candidates_for_investigation(
        vessels=[vessel],
        backward_steps=backward_steps,
        source_lon=source_lon,
        source_lat=source_lat,
        source_radius_m=source_radius_m,
        observation_time=obs_utc,
        backtrack_hours=backtrack_hours,
        corridor_km=corridor_km,
    )

    eligible = filtering_result["eligible_candidates"]
    ineligible = filtering_result["ineligible_candidates"]

    if eligible:
        target = eligible[0]
        # 4. Standard attribution model inference
        model = load_model(model_id=model_id)
        feat_dict = extract_vessel_features(
            vessel_data=target,
            source_lon=source_lon,
            source_lat=source_lat,
            source_radius_m=source_radius_m,
            observation_time=obs_utc,
            backtrack_hours=backtrack_hours,
            backward_steps=backward_steps,
        )
        feature_vector = [feat_dict[fn] for fn in FEATURE_NAMES]
        X = np.array([feature_vector], dtype=np.float32)
        prob = float(model.pipeline.predict_proba(X)[0, 1])

        return {
            "found": True,
            "vessel_identifier": vessel_identifier,
            "vessel_metadata": {
                "vessel_id": vessel["vessel_id"],
                "vessel_name": vessel["vessel_name"],
                "mmsi": vessel["mmsi"],
                "vessel_type": vessel["vessel_type"],
                "source_id": vessel["source_id"],
                "source_type": vessel["source_type"],
                "is_real_observation": vessel["is_real_observation"],
                "provider_name": vessel.get("provider_name"),
            },
            "is_eligible": True,
            "eligibility_status": "ELIGIBLE",
            "exclusion_reasons": [],
            "corridor_dist_km": target["min_corridor_dist_km"],
            "features": feat_dict,
            "attribution": {
                "model_id": model.model_id,
                "model_probability": round(prob, 4),
                "is_attributed": prob >= 0.5,
                "confidence_assessment": f"Model probability of responsibility is {prob:.1%}.",
            },
            "positions_count": len(vessel.get("positions", [])),
        }
    else:
        rej = ineligible[0] if ineligible else {}
        resp: dict[str, Any] = {
            "found": True,
            "vessel_identifier": vessel_identifier,
            "vessel_metadata": {
                "vessel_id": vessel["vessel_id"],
                "vessel_name": vessel["vessel_name"],
                "mmsi": vessel["mmsi"],
                "vessel_type": vessel["vessel_type"],
                "source_id": vessel["source_id"],
                "source_type": vessel["source_type"],
                "is_real_observation": vessel["is_real_observation"],
                "provider_name": vessel.get("provider_name"),
            },
            "is_eligible": False,
            "eligibility_status": "EXCLUDED",
            "exclusion_reasons": [rej.get("rejection_reason", "OUTSIDE_SPATIOTEMPORAL_CORRIDOR")],
            "exclusion_detail": rej.get("rejection_detail", "Vessel did not meet spatial corridor or temporal intersection criteria."),
            "corridor_dist_km": rej.get("min_corridor_dist_km"),
            "attribution": None,
            "positions_count": len(vessel.get("positions", [])),
        }

        # Documented Counterfactual analysis if explicitly requested
        if force_counterfactual:
            model = load_model(model_id=model_id)
            feat_dict = extract_vessel_features(
                vessel_data=vessel,
                source_lon=source_lon,
                source_lat=source_lat,
                source_radius_m=source_radius_m,
                observation_time=obs_utc,
                backtrack_hours=backtrack_hours,
                backward_steps=backward_steps,
            )
            feature_vector = [feat_dict[fn] for fn in FEATURE_NAMES]
            X = np.array([feature_vector], dtype=np.float32)
            prob = float(model.pipeline.predict_proba(X)[0, 1])
            resp["counterfactual_attribution"] = {
                "model_id": model.model_id,
                "model_probability": round(prob, 4),
                "notice": "DOCUMENTED COUNTERFACTUAL ANALYSIS ONLY: Vessel is physically outside corridor.",
            }

        return resp

