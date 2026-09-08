"""Hermetic tests for MARIS Stage E1 — Candidate Vessel Generation.

All tests are completely offline and hermetic.
Verifies spatial proximity, temporal clamping, zero-fabrication guarantees,
identity handling, missing data behaviors, GeoJSON export, and API endpoints.
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.acquisition.providers.ais import (
    ARTIFACT_NAME,
    ARTIFACT_SCHEMA,
    AisAcquisitionProvider,
    StaticAisAdapter,
    UnconfiguredAisAdapter,
)
from app.acquisition.registry import InMemoryAssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.core.config import Settings
from app.main import app
from app.models.asset import Asset
from app.models.common import AssetType, Provenance
from app.models.source_estimation import SourceEstimateResult
from app.models.vessel import (
    CandidateGenerationStatus,
    CandidateVessel,
    CandidateVesselGenerationResult,
)
from app.services.candidate_vessels import (
    AisValidationFailureError,
    CandidateVesselError,
    derive_spatial_bounding_box,
    derive_temporal_window,
    generate_candidate_vessels_for_spill,
    load_source_estimate_from_asset,
    point_in_polygon,
)
from app.services.source_estimation import generate_source_candidate_polygon


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class CandidateVesselsBaseTest(unittest.TestCase):
    """Base setup with synthetic source estimate and temporary directory."""

    def setUp(self) -> None:
        default_asset_registry._assets.clear()
        default_asset_registry._order.clear()

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp_dir.name)

        # Investigation and spill identifiers
        self.investigation_id = "inv-cand-test"
        self.spill_id = "spill-cand-001"

        # Coordinates: center at lon=2.0, lat=51.0
        self.center_lon = 2.0
        self.center_lat = 51.0
        self.uncertainty_radius_km = 5.0  # 5 km radius

        # Times
        self.source_time = datetime(2024, 6, 2, 0, 0, tzinfo=timezone.utc)
        self.observation_time = datetime(2024, 6, 2, 6, 0, tzinfo=timezone.utc)

        # 32-vertex regular polygon for 5000 m radius
        self.source_polygon = generate_source_candidate_polygon(
            center_lon=self.center_lon,
            center_lat=self.center_lat,
            radius_m=self.uncertainty_radius_km * 1000.0,
            num_vertices=32,
        )

        # Create synthetic SourceEstimateResult
        self.source_estimate = SourceEstimateResult(
            id=f"source-{self.spill_id}-{self.investigation_id}",
            investigation_id=self.investigation_id,
            spill_detection_id=self.spill_id,
            wind_asset_id="wind-asset-001",
            current_asset_id="curr-asset-001",
            asset_id="drift-asset-001",
            model_version="leeway_euler_backward_v1",
            observation_time=self.observation_time,
            origin_lon=2.2,
            origin_lat=51.1,
            source_time=self.source_time,
            source_point_lon=self.center_lon,
            source_point_lat=self.center_lat,
            lookback_hours=6.0,
            step_hours=1.0,
            leeway_fraction=0.035,
            source_uncertainty_radius_km=self.uncertainty_radius_km,
            steps=[],
            source_zone_geometry=self.source_polygon,
            metadata={"test": "fixture"},
        )

        # Save source estimate as a registered DRIFT_PRODUCT asset
        self.source_geojson_path = self.work_dir / "source_candidate_zone.geojson"
        feat_collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[2.2, 51.1], [self.center_lon, self.center_lat]],
                    },
                    "properties": {
                        "feature_kind": "backward_drift_centerline",
                        "investigation_id": self.investigation_id,
                        "spill_detection_id": self.spill_id,
                        "observation_time": self.observation_time.isoformat(),
                        "source_time": self.source_time.isoformat(),
                        "lookback_hours": 6.0,
                    },
                },
                {
                    "type": "Feature",
                    "geometry": self.source_polygon,
                    "properties": {
                        "feature_kind": "source_candidate_zone",
                        "investigation_id": self.investigation_id,
                        "spill_detection_id": self.spill_id,
                        "source_time": self.source_time.isoformat(),
                        "center_lon": self.center_lon,
                        "center_lat": self.center_lat,
                        "uncertainty_radius_km": self.uncertainty_radius_km,
                    },
                },
            ],
        }
        self.source_geojson_path.write_text(json.dumps(feat_collection, indent=2), encoding="utf-8")

        self.source_asset = default_asset_registry.register(
            self.investigation_id,
            "source_estimation",
            AcquiredArtifact(
                asset_type=AssetType.DRIFT_PRODUCT,
                location=str(self.source_geojson_path),
                source="test",
                provenance=Provenance(
                    product_id=self.spill_id,
                    extra={
                        "source_id": self.source_estimate.id,
                        "source_estimate_id": self.source_estimate.id,
                        "observation_time": self.observation_time.isoformat(),
                        "source_time": self.source_time.isoformat(),
                        "lookback_hours": 6.0,
                        "step_hours": 1.0,
                        "leeway_fraction": 0.035,
                        "source_uncertainty_radius_km": self.uncertainty_radius_km,
                    },
                ),
                metadata={"source_id": self.source_estimate.id},
            ),
        )

        # Register spill asset
        self.spill_asset = default_asset_registry.register(
            self.investigation_id,
            "spill_detector",
            AcquiredArtifact(
                asset_type=AssetType.SPILL_GEOMETRY,
                location=str(self.work_dir / "spill.geojson"),
                source="test",
                acquisition_time=self.observation_time,
                provenance=Provenance(product_id=self.spill_id),
                metadata={
                    "detected": True,
                    "centroid": {"lon": 2.2, "lat": 51.1},
                    "area": 50000.0,
                },
            ),
        )

        self.client = TestClient(app)

    def tearDown(self) -> None:
        default_asset_registry._assets.clear()
        default_asset_registry._order.clear()
        self.tmp_dir.cleanup()


    def _create_ais_artifact(
        self,
        records: list[dict],
        filename: str = ARTIFACT_NAME,
        schema: str = ARTIFACT_SCHEMA,
    ) -> Path:
        """Write compliant maris.ais.positions.v1 JSON artifact."""
        target = self.work_dir / filename
        body = {
            "schema": schema,
            "query": {
                "investigation_id": self.investigation_id,
                "time_window": {
                    "start": (self.source_time - timedelta(hours=4)).isoformat(),
                    "end": self.observation_time.isoformat(),
                },
                "bounding_box": {"west": 1.8, "south": 50.8, "east": 2.2, "north": 51.2},
            },
            "records": records,
        }
        target.write_text(json.dumps(body, indent=2), encoding="utf-8")
        return target

    def _register_ais_asset(self, records: list[dict], asset_id: str = "asset-ais-001") -> Asset:
        path = self._create_ais_artifact(records, filename=f"{asset_id}.json")
        artifact = AcquiredArtifact(
            asset_type=AssetType.VESSEL_TRACK,
            location=str(path),
            source="test_ais",
            acquisition_time=self.source_time,
            provenance=Provenance(
                product_id=ARTIFACT_SCHEMA,
                extra={"record_count": len(records)},
            ),
            metadata={"record_count": len(records)},
        )
        return default_asset_registry.register(self.investigation_id, "ais", artifact)


class TestCandidateVesselSpatialAndTemporalPhysics(CandidateVesselsBaseTest):
    """Test cases 1 to 8: Spatial containment, boundaries, buffers, temporal limits."""

    def test_1_point_inside_source_zone(self) -> None:
        """Test 1: Point dead-center of source zone qualifies as inside_source_zone=True."""
        records = [
            {
                "timestamp": self.source_time.isoformat(),
                "lat": self.center_lat,
                "lon": self.center_lon,
                "mmsi": "111222333",
                "imo": "1234567",
                "vessel_name": "CENTER SHIP",
                "sog": 10.0,
                "cog": 90.0,
            }
        ]
        asset = self._register_ais_asset(records, "asset-center")
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            output_dir=self.work_dir,
        )
        self.assertEqual(result.status, CandidateGenerationStatus.COMPLETED)
        self.assertEqual(result.candidate_count, 1)
        c = result.candidates[0]
        self.assertTrue(c.inside_source_zone)
        self.assertAlmostEqual(c.min_distance_to_source_center_km, 0.0, places=2)
        self.assertEqual(c.distance_to_zone_boundary_km, 0.0)

    def test_2_point_on_boundary(self) -> None:
        """Test 2: Point right at the 5 km boundary of the source zone qualifies."""
        # 5 km north: 5.0 / 111.32 ≈ 0.044915 degrees latitude
        boundary_lat = self.center_lat + (self.uncertainty_radius_km / 111.32)
        records = [
            {
                "timestamp": self.source_time.isoformat(),
                "lat": boundary_lat,
                "lon": self.center_lon,
                "mmsi": "222333444",
                "vessel_name": "BOUNDARY SHIP",
            }
        ]
        asset = self._register_ais_asset(records, "asset-boundary")
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            output_dir=self.work_dir,
        )
        self.assertEqual(result.candidate_count, 1)
        c = result.candidates[0]
        self.assertTrue(c.inside_source_zone)
        self.assertAlmostEqual(c.min_distance_to_source_center_km, 5.0, places=1)

    def test_3_point_outside_but_within_buffer(self) -> None:
        """Test 3: Point outside source zone (6 km away) qualifies when buffer=2 km."""
        # 6 km north (outside 5 km radius, but within 5 + 2 = 7 km)
        outside_lat = self.center_lat + (6.0 / 111.32)
        records = [
            {
                "timestamp": self.source_time.isoformat(),
                "lat": outside_lat,
                "lon": self.center_lon,
                "mmsi": "333444555",
                "vessel_name": "NEAR SHIP",
            }
        ]
        asset = self._register_ais_asset(records, "asset-buffer")
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            spatial_buffer_km=2.0,
            output_dir=self.work_dir,
        )
        self.assertEqual(result.candidate_count, 1)
        c = result.candidates[0]
        self.assertFalse(c.inside_source_zone)
        self.assertGreater(c.min_distance_to_source_center_km, 5.0)
        self.assertLessEqual(c.min_distance_to_source_center_km, 7.0)
        self.assertGreater(c.distance_to_zone_boundary_km, 0.0)

    def test_4_point_outside_buffer_excluded(self) -> None:
        """Test 4: Point 10 km away is excluded when buffer=2 km (threshold 7 km)."""
        far_lat = self.center_lat + (10.0 / 111.32)
        records = [
            {
                "timestamp": self.source_time.isoformat(),
                "lat": far_lat,
                "lon": self.center_lon,
                "mmsi": "444555666",
                "vessel_name": "FAR SHIP",
            }
        ]
        asset = self._register_ais_asset(records, "asset-far")
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            spatial_buffer_km=2.0,
            output_dir=self.work_dir,
        )
        self.assertEqual(result.status, CandidateGenerationStatus.NO_CANDIDATES_FOUND)
        self.assertEqual(result.candidate_count, 0)
        self.assertEqual(result.total_vessels_checked, 1)

    def test_5_exact_source_time_match(self) -> None:
        """Test 5: Vessel observation at exact source_time has time_offset_from_source_hours == 0.0."""
        records = [
            {
                "timestamp": self.source_time.isoformat(),
                "lat": self.center_lat,
                "lon": self.center_lon,
                "mmsi": "555666777",
            }
        ]
        asset = self._register_ais_asset(records, "asset-exact-time")
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            output_dir=self.work_dir,
        )
        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(result.candidates[0].time_offset_from_source_hours, 0.0)

    def test_6_valid_point_plus_1_hour(self) -> None:
        """Test 6: Vessel observation at source_time + 1.0 hour has time_offset == +1.0."""
        t_plus_1 = self.source_time + timedelta(hours=1.0)
        records = [
            {
                "timestamp": t_plus_1.isoformat(),
                "lat": self.center_lat,
                "lon": self.center_lon,
                "mmsi": "666777888",
            }
        ]
        asset = self._register_ais_asset(records, "asset-plus-1")
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            temporal_window_hours=2.0,
            output_dir=self.work_dir,
        )
        self.assertEqual(result.candidate_count, 1)
        self.assertAlmostEqual(result.candidates[0].time_offset_from_source_hours, 1.0, places=3)

    def test_7_point_outside_plus_minus_2h_excluded(self) -> None:
        """Test 7: Observations outside ±2 hours (e.g. +3.0 h or -3.0 h) are excluded."""
        t_plus_3 = self.source_time + timedelta(hours=3.0)
        records = [
            {
                "timestamp": t_plus_3.isoformat(),
                "lat": self.center_lat,
                "lon": self.center_lon,
                "mmsi": "777888999",
            }
        ]
        asset = self._register_ais_asset(records, "asset-outside-win")
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            temporal_window_hours=2.0,
            output_dir=self.work_dir,
        )
        self.assertEqual(result.status, CandidateGenerationStatus.NO_CANDIDATES_FOUND)
        self.assertEqual(result.candidate_count, 0)

    def test_8_temporal_end_clamped_to_observation_time(self) -> None:
        """Test 8: Temporal search window cannot extend beyond spill observation_time."""
        # Set source_time 1.0 hour prior to observation_time
        close_source_time = self.observation_time - timedelta(hours=1.0)
        start, end = derive_temporal_window(
            source_time=close_source_time,
            observation_time=self.observation_time,
            temporal_window_hours=3.0,
        )
        # Without clamp, end would be close_source_time + 3h = obs + 2h.
        # With clamp, end must equal observation_time.
        self.assertEqual(end, self.observation_time)
        self.assertEqual(start, close_source_time - timedelta(hours=3.0))


class TestCandidateVesselGroupingAndIdentity(CandidateVesselsBaseTest):
    """Test cases 9 to 15: Grouping, CPA selection, zero-fabrication, metadata handling."""

    def test_9_multiple_pings_grouped_into_one_candidate(self) -> None:
        """Test 9: Multiple AIS pings for one MMSI are grouped into exactly 1 candidate."""
        records = [
            {
                "timestamp": (self.source_time - timedelta(minutes=30)).isoformat(),
                "lat": self.center_lat + 0.01,
                "lon": self.center_lon + 0.01,
                "mmsi": "999000111",
                "vessel_name": "MULTI SHIP",
            },
            {
                "timestamp": self.source_time.isoformat(),
                "lat": self.center_lat,
                "lon": self.center_lon,
                "mmsi": "999000111",
                "vessel_name": "MULTI SHIP",
            },
            {
                "timestamp": (self.source_time + timedelta(minutes=30)).isoformat(),
                "lat": self.center_lat - 0.01,
                "lon": self.center_lon - 0.01,
                "mmsi": "999000111",
                "vessel_name": "MULTI SHIP",
            },
        ]
        asset = self._register_ais_asset(records, "asset-multi-pings")
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            output_dir=self.work_dir,
        )
        self.assertEqual(result.candidate_count, 1)
        c = result.candidates[0]
        self.assertEqual(c.mmsi, "999000111")
        self.assertEqual(c.observed_positions_count, 3)

    def test_10_cpa_selected_from_actual_observed_ping(self) -> None:
        """Test 10: CPA is selected from the actual observation with minimum distance to center."""
        # Ping 1: 3 km away; Ping 2: 1 km away; Ping 3: 4 km away
        lat_1km = self.center_lat + (1.0 / 111.32)
        lat_3km = self.center_lat + (3.0 / 111.32)
        lat_4km = self.center_lat + (4.0 / 111.32)
        t2 = self.source_time + timedelta(minutes=15)

        records = [
            {
                "timestamp": (self.source_time - timedelta(minutes=15)).isoformat(),
                "lat": lat_3km,
                "lon": self.center_lon,
                "mmsi": "123123123",
                "speed_over_ground": 15.0,
            },
            {
                "timestamp": t2.isoformat(),
                "lat": lat_1km,
                "lon": self.center_lon,
                "mmsi": "123123123",
                "speed_over_ground": 12.0,
            },
            {
                "timestamp": (self.source_time + timedelta(minutes=45)).isoformat(),
                "lat": lat_4km,
                "lon": self.center_lon,
                "mmsi": "123123123",
                "speed_over_ground": 14.0,
            },
        ]
        asset = self._register_ais_asset(records, "asset-cpa")
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            output_dir=self.work_dir,
        )
        c = result.candidates[0]
        self.assertAlmostEqual(c.min_distance_to_source_center_km, 1.0, places=1)
        self.assertEqual(c.closest_position_time, t2)
        self.assertEqual(c.speed_over_ground, 12.0)

    def test_11_all_retained_raw_positions_preserved(self) -> None:
        """Test 11: All retained raw observations are preserved in raw_positions."""
        records = [
            {
                "timestamp": (self.source_time - timedelta(minutes=10)).isoformat(),
                "lat": self.center_lat,
                "lon": self.center_lon,
                "mmsi": "234234234",
            },
            {
                "timestamp": (self.source_time + timedelta(minutes=10)).isoformat(),
                "lat": self.center_lat + 0.01,
                "lon": self.center_lon + 0.01,
                "mmsi": "234234234",
            },
        ]
        asset = self._register_ais_asset(records, "asset-preserve")
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            output_dir=self.work_dir,
        )
        c = result.candidates[0]
        self.assertEqual(len(c.raw_positions), 2)
        self.assertEqual(c.raw_positions[0].lon, self.center_lon)
        self.assertEqual(c.raw_positions[1].lon, self.center_lon + 0.01)

    def test_12_no_synthetic_points_created_across_ais_gaps(self) -> None:
        """Test 12: ZERO-FABRICATION INVARIANT. A vessel with points on either side of the zone

        does NOT have synthetic points interpolated into the zone.
        """
        # Point A: 10 km west (outside 5 km zone)
        # Point B: 10 km east (outside 5 km zone)
        # Even though a straight line connecting them crosses the zone,
        # NO observed points fall inside the zone, so the vessel MUST NOT be a candidate!
        lon_west = self.center_lon - (10.0 / (111.32 * math.cos(math.radians(self.center_lat))))
        lon_east = self.center_lon + (10.0 / (111.32 * math.cos(math.radians(self.center_lat))))
        records = [
            {
                "timestamp": (self.source_time - timedelta(hours=1)).isoformat(),
                "lat": self.center_lat,
                "lon": lon_west,
                "mmsi": "345345345",
                "vessel_name": "TRANSIT SHIP",
            },
            {
                "timestamp": (self.source_time + timedelta(hours=1)).isoformat(),
                "lat": self.center_lat,
                "lon": lon_east,
                "mmsi": "345345345",
                "vessel_name": "TRANSIT SHIP",
            },
        ]
        asset = self._register_ais_asset(records, "asset-gap-zero-fab")
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            spatial_buffer_km=0.0,
            output_dir=self.work_dir,
        )
        # MUST BE NO CANDIDATES FOUND! No synthetic transit point was fabricated!
        self.assertEqual(result.status, CandidateGenerationStatus.NO_CANDIDATES_FOUND)
        self.assertEqual(result.candidate_count, 0)

    def test_13_mmsi_only_vessel(self) -> None:
        """Test 13: Vessel with MMSI only (no IMO, no vessel_name) is valid."""
        records = [
            {
                "timestamp": self.source_time.isoformat(),
                "lat": self.center_lat,
                "lon": self.center_lon,
                "mmsi": "456456456",
            }
        ]
        asset = self._register_ais_asset(records, "asset-mmsi-only")
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            output_dir=self.work_dir,
        )
        c = result.candidates[0]
        self.assertEqual(c.mmsi, "456456456")
        self.assertIsNone(c.imo)
        self.assertIsNone(c.vessel_name)

    def test_14_missing_imo_and_name_retains_identity(self) -> None:
        """Test 14: Vessel with IMO only (no MMSI) or name only is identified properly."""
        records = [
            {
                "timestamp": self.source_time.isoformat(),
                "lat": self.center_lat,
                "lon": self.center_lon,
                "imo": "9876543",
                "vessel_name": "IMO ONLY SHIP",
            }
        ]
        asset = self._register_ais_asset(records, "asset-imo-only")
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            output_dir=self.work_dir,
        )
        c = result.candidates[0]
        self.assertIsNone(c.mmsi)
        self.assertEqual(c.imo, "9876543")
        self.assertEqual(c.vessel_name, "IMO ONLY SHIP")

    def test_15_malformed_optional_sog_cog_handled_gracefully(self) -> None:
        """Test 15: Malformed SOG, COG, or heading values do not crash generation."""
        records = [
            {
                "timestamp": self.source_time.isoformat(),
                "lat": self.center_lat,
                "lon": self.center_lon,
                "mmsi": "567567567",
                "speed_over_ground": "invalid-sog",
                "course_over_ground": 450.0,  # invalid range
                "heading": -10.0,  # invalid range
            }
        ]
        # Bypassing strict validator for raw record injection test
        target = self._create_ais_artifact(records, "asset-malformed-kinematics.json")
        artifact = AcquiredArtifact(
            asset_type=AssetType.VESSEL_TRACK,
            location=str(target),
            source="test",
            provenance=Provenance(product_id="test"),
        )
        asset = default_asset_registry.register(self.investigation_id, "ais", artifact)

        # Generating candidate directly from service
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=None,  # pass None so it finds the registered asset without failing strict validator
            output_dir=self.work_dir,
        )
        self.assertEqual(result.candidate_count, 1)
        c = result.candidates[0]
        self.assertIsNone(c.speed_over_ground)
        self.assertIsNone(c.course_over_ground)
        self.assertIsNone(c.heading)


class TestCandidateVesselMissingDataAndValidation(CandidateVesselsBaseTest):
    """Test cases 16 to 18: Empty artifacts, unconfigured adapters, invalid assets."""

    def test_16_empty_ais_artifact(self) -> None:
        """Test 16: An AIS artifact with zero position records yields NO_CANDIDATES_FOUND."""
        asset = self._register_ais_asset([], "asset-empty")
        result, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            output_dir=self.work_dir,
        )
        self.assertEqual(result.status, CandidateGenerationStatus.NO_CANDIDATES_FOUND)
        self.assertEqual(result.candidate_count, 0)

    def test_17_unconfigured_adapter(self) -> None:
        """Test 17: When no AIS asset exists and adapter is unconfigured, returns AIS_DATA_UNAVAILABLE."""
        clean_registry = InMemoryAssetRegistry()
        unconfigured_provider = AisAcquisitionProvider(
            settings=Settings(data_dir=self.work_dir, ais_adapter_id="unconfigured"),
            adapter=UnconfiguredAisAdapter(),
        )
        result, derived_asset = generate_candidate_vessels_for_spill(
            investigation_id="inv-unconf",
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=None,
            registry=clean_registry,
            ais_provider=unconfigured_provider,
            output_dir=self.work_dir,
        )
        self.assertEqual(result.status, CandidateGenerationStatus.AIS_DATA_UNAVAILABLE)
        self.assertEqual(result.candidate_count, 0)
        self.assertIsNone(derived_asset)

    def test_18_invalid_ais_artifact(self) -> None:
        """Test 18: If explicitly supplied AIS asset fails validation, raises AisValidationFailureError."""
        corrupt_path = self.work_dir / "corrupt.json"
        corrupt_path.write_text("{\"schema\": \"maris.ais.positions.v1\", \"records\": [\"malformed\"]}", encoding="utf-8")
        corrupt_asset = default_asset_registry.register(
            self.investigation_id,
            "ais",
            AcquiredArtifact(
                asset_type=AssetType.VESSEL_TRACK,
                location=str(corrupt_path),
                source="test",
                provenance=Provenance(product_id="test"),
            ),
        )
        with self.assertRaises(AisValidationFailureError):
            generate_candidate_vessels_for_spill(
                investigation_id=self.investigation_id,
                spill_id=self.spill_id,
                source_estimate=self.source_estimate,
                ais_asset_id=corrupt_asset.id,
                output_dir=self.work_dir,
            )


class TestCandidateVesselApiAndArtifacts(CandidateVesselsBaseTest):
    """Test cases 19 to 25: API routes, artifacts, DOCUMENT asset registration, determinism."""

    def test_19_api_success_200(self) -> None:
        """Test 19: POST /api/v1/investigations/.../candidates returns 200 with valid candidates."""
        records = [
            {
                "timestamp": self.source_time.isoformat(),
                "lat": self.center_lat,
                "lon": self.center_lon,
                "mmsi": "789789789",
                "vessel_name": "API SHIP",
            }
        ]
        asset = self._register_ais_asset(records, "asset-api-200")
        resp = self.client.post(
            f"/api/v1/investigations/{self.investigation_id}/spills/{self.spill_asset.id}/candidates",
            json={
                "source_estimate_id": self.source_asset.id,
                "ais_asset_id": asset.id,
                "temporal_window_hours": 2.0,
                "spatial_buffer_km": 1.0,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["candidate_count"], 1)
        self.assertEqual(data["candidates"][0]["mmsi"], "789789789")

    def test_20_api_404_missing_spill_or_source(self) -> None:
        """Test 20: API returns 404 for nonexistent spill, source estimate, or AIS asset."""
        # Nonexistent spill
        resp = self.client.post(
            f"/api/v1/investigations/{self.investigation_id}/spills/nonexistent-spill/candidates",
            json={"source_estimate_id": self.source_asset.id},
        )
        self.assertEqual(resp.status_code, 404)

        # Nonexistent source estimate
        resp = self.client.post(
            f"/api/v1/investigations/{self.investigation_id}/spills/{self.spill_asset.id}/candidates",
            json={"source_estimate_id": "nonexistent-source"},
        )
        self.assertEqual(resp.status_code, 404)

        # Nonexistent AIS asset
        resp = self.client.post(
            f"/api/v1/investigations/{self.investigation_id}/spills/{self.spill_asset.id}/candidates",
            json={
                "source_estimate_id": self.source_asset.id,
                "ais_asset_id": "nonexistent-ais",
            },
        )
        self.assertEqual(resp.status_code, 404)

    def test_21_api_validation_failure_422(self) -> None:
        """Test 21: API returns 422 for invalid parameters (e.g. negative buffer or invalid AIS data)."""
        resp = self.client.post(
            f"/api/v1/investigations/{self.investigation_id}/spills/{self.spill_asset.id}/candidates",
            json={
                "source_estimate_id": self.source_asset.id,
                "spatial_buffer_km": -5.0,
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_22_geojson_feature_collection_validity(self) -> None:
        """Test 22: Written GeoJSON file is a valid RFC 7946 FeatureCollection with CPA and track features."""
        records = [
            {
                "timestamp": self.source_time.isoformat(),
                "lat": self.center_lat,
                "lon": self.center_lon,
                "mmsi": "890890890",
            },
            {
                "timestamp": (self.source_time + timedelta(minutes=10)).isoformat(),
                "lat": self.center_lat + 0.01,
                "lon": self.center_lon + 0.01,
                "mmsi": "890890890",
            },
        ]
        asset = self._register_ais_asset(records, "asset-geojson")
        result, derived_asset = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            output_dir=self.work_dir,
        )
        self.assertIsNotNone(derived_asset)
        geojson_path = Path(derived_asset.location)
        self.assertTrue(geojson_path.exists())

        data = json.loads(geojson_path.read_text(encoding="utf-8"))
        self.assertEqual(data["type"], "FeatureCollection")
        self.assertEqual(len(data["features"]), 2)  # 1 CPA Point feature + 1 track LineString feature
        self.assertEqual(data["features"][0]["geometry"]["type"], "Point")
        self.assertEqual(data["features"][1]["geometry"]["type"], "LineString")
        self.assertTrue(data["features"][1]["properties"]["zero_fabrication"])

    def test_23_document_asset_registration(self) -> None:
        """Test 23: Derived artifact is registered as AssetType.DOCUMENT with asset_type='candidate_vessels'."""
        records = [
            {
                "timestamp": self.source_time.isoformat(),
                "lat": self.center_lat,
                "lon": self.center_lon,
                "mmsi": "901901901",
            }
        ]
        asset = self._register_ais_asset(records, "asset-doc-reg")
        result, derived_asset = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            output_dir=self.work_dir,
        )
        self.assertIsNotNone(derived_asset)
        # CRITICAL ASSERTION: Must be DOCUMENT, NOT VESSEL_TRACK
        self.assertEqual(derived_asset.type, AssetType.DOCUMENT)
        self.assertEqual(derived_asset.metadata["asset_type"], "candidate_vessels")

    def test_24_provenance_linkage(self) -> None:
        """Test 24: Provenance accurately records source_estimate_id, spill_id, and query parameters."""
        records = [
            {
                "timestamp": self.source_time.isoformat(),
                "lat": self.center_lat,
                "lon": self.center_lon,
                "mmsi": "012012012",
            }
        ]
        asset = self._register_ais_asset(records, "asset-prov")
        result, derived_asset = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            temporal_window_hours=2.5,
            spatial_buffer_km=1.5,
            output_dir=self.work_dir,
        )
        self.assertIsNotNone(derived_asset)
        extra = derived_asset.provenance.extra
        self.assertEqual(extra["source_estimate_id"], self.source_estimate.id)
        self.assertEqual(extra["spill_detection_id"], self.spill_id)
        self.assertEqual(extra["ais_asset_id"], asset.id)
        self.assertEqual(extra["temporal_window_hours"], 2.5)
        self.assertEqual(extra["spatial_buffer_km"], 1.5)

    def test_25_deterministic_repeated_execution(self) -> None:
        """Test 25: Repeated execution on identical inputs yields bitwise identical candidate metrics."""
        records = [
            {
                "timestamp": (self.source_time - timedelta(minutes=15)).isoformat(),
                "lat": self.center_lat + 0.01,
                "lon": self.center_lon,
                "mmsi": "345678901",
                "speed_over_ground": 11.5,
            },
            {
                "timestamp": (self.source_time + timedelta(minutes=15)).isoformat(),
                "lat": self.center_lat - 0.01,
                "lon": self.center_lon,
                "mmsi": "345678901",
                "speed_over_ground": 11.2,
            },
            {
                "timestamp": self.source_time.isoformat(),
                "lat": self.center_lat,
                "lon": self.center_lon,
                "mmsi": "456789012",
                "speed_over_ground": 8.0,
            },
        ]
        asset = self._register_ais_asset(records, "asset-repeat")
        res1, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            output_dir=self.work_dir,
        )
        res2, _ = generate_candidate_vessels_for_spill(
            investigation_id=self.investigation_id,
            spill_id=self.spill_id,
            source_estimate=self.source_estimate,
            ais_asset_id=asset.id,
            output_dir=self.work_dir,
        )
        self.assertEqual(res1.candidate_count, res2.candidate_count)
        self.assertEqual(
            [c.candidate_id for c in res1.candidates],
            [c.candidate_id for c in res2.candidates],
        )
        self.assertEqual(
            [c.min_distance_to_source_center_km for c in res1.candidates],
            [c.min_distance_to_source_center_km for c in res2.candidates],
        )


if __name__ == "__main__":
    unittest.main()
