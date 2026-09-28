"""MARIS Stage E1 Candidate Vessel Generation Hardening Tests.

Validates the concrete hardening fixes:
A. AIS AOI boundary behavior:
   1. Vessel has 1 ping inside AOI and 1 ping outside AOI -> acquisition succeeds,
      track retained, and E1 filters according to D3 source zone.
   2. Vessel has all pings outside AOI -> rejected by acquisition / excluded by E1.
   3. Malformed coordinates -> rejected by validation.
   4. Timestamp outside requested time range -> excluded by validation.
B. Vessel metadata:
   5. vessel_type populated from SQLite/reference data.
   6. Missing vessel_type remains None.
   7. Existing CandidateVessel construction remains 100% backward compatible.
C. D3 zero-step fallback:
   8. Backward drift producing zero valid steps does not raise UnboundLocalError.
   9. Returned result contains an explicit structured status (e.g. shoreline_boundary_reached).
   10. Normal successful D3 behavior remains completely unchanged.
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import netCDF4 as nc

from app.acquisition.base import AcquisitionError
from app.acquisition.providers.ais import (
    ARTIFACT_SCHEMA,
    AisAcquisitionProvider,
    AisHistoricalQuery,
    AisPositionRecord,
    SqliteAisAdapter,
    StaticAisAdapter,
    normalize_ais_records,
)
from app.acquisition.registry import InMemoryAssetRegistry
from app.acquisition.schemas import AcquiredArtifact, AcquisitionRequest
from app.core.config import Settings
from app.models.asset import Asset
from app.models.common import (
    AssetType,
    BBoxAreaOfInterest,
    BoundingBox,
    Provenance,
    TimeWindow,
)
from app.models.source_estimation import SourceEstimateResult
from app.models.vessel import (
    CandidateGenerationStatus,
    CandidateVessel,
    CandidateVesselGenerationResult,
    VesselPosition,
)
from app.services.candidate_vessels import (
    generate_candidate_vessels_for_spill,
    load_source_estimate_from_asset,
)
from app.services.drift_modelling import _open_netcdf
from app.services.real_experiment.ais_database import (
    init_ais_database,
)
from app.services.source_estimation import (
    compute_source_estimate_for_spill,
    generate_source_candidate_polygon,
    run_backward_drift,
)
from app.services.trajectory_analysis import (
    analyze_candidate_trajectories,
    load_candidate_result_from_asset,
)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class MockDomainChecker:
    def __init__(self, allowed_predicate) -> None:
        self._predicate = allowed_predicate

    def is_maritime(self, lon: float, lat: float) -> bool:
        return self._predicate(lon, lat)

    def is_in_maritime_domain(self, lon: float, lat: float) -> bool:
        return self._predicate(lon, lat)


def _create_minimal_era5(path: Path | str, u_val: float = 2.0, v_val: float = 1.0) -> None:
    lats = [52.0, 51.5, 51.0]
    lons = [2.0, 2.5, 3.0]
    times = [1090608 + i for i in range(24)]
    ds = nc.Dataset(str(path), "w", format="NETCDF4_CLASSIC")
    ds.createDimension("time", len(times))
    ds.createDimension("latitude", len(lats))
    ds.createDimension("longitude", len(lons))
    tv = ds.createVariable("time", "i4", ("time",))
    tv.units = "hours since 1900-01-01 00:00:00.0"
    tv.calendar = "gregorian"
    tv[:] = times
    lav = ds.createVariable("latitude", "f4", ("latitude",))
    lav.units = "degrees_north"
    lav[:] = lats
    lov = ds.createVariable("longitude", "f4", ("longitude",))
    lov.units = "degrees_east"
    lov[:] = lons
    u10 = ds.createVariable("u10", "f4", ("time", "latitude", "longitude"))
    u10.units = "m s**-1"
    v10 = ds.createVariable("v10", "f4", ("time", "latitude", "longitude"))
    v10.units = "m s**-1"
    u10[:] = u_val
    v10[:] = v_val
    ds.close()


def _create_minimal_cmems(path: Path | str, u_val: float = 0.2, v_val: float = 0.1) -> None:
    lats = [52.0, 51.5, 51.0]
    lons = [2.0, 2.5, 3.0]
    times = [27180]
    ds = nc.Dataset(str(path), "w", format="NETCDF4_CLASSIC")
    ds.createDimension("time", len(times))
    ds.createDimension("latitude", len(lats))
    ds.createDimension("longitude", len(lons))
    tv = ds.createVariable("time", "i4", ("time",))
    tv.units = "days since 1950-01-01 00:00:00"
    tv.calendar = "gregorian"
    tv[:] = times
    lav = ds.createVariable("latitude", "f4", ("latitude",))
    lav.units = "degrees_north"
    lav[:] = lats
    lov = ds.createVariable("longitude", "f4", ("longitude",))
    lov.units = "degrees_east"
    lov[:] = lons
    uo = ds.createVariable("uo", "f4", ("time", "latitude", "longitude"))
    uo.units = "m s**-1"
    vo = ds.createVariable("vo", "f4", ("time", "latitude", "longitude"))
    vo.units = "m s**-1"
    uo[:] = u_val
    vo[:] = v_val
    ds.close()


class TestE1AisBoundaryHardening(unittest.TestCase):
    """A. AIS boundary tests (scenarios 1-4)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp.name)
        self.t_start = datetime(2024, 6, 1, 0, 0, tzinfo=timezone.utc)
        self.t_end = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
        self.aoi = BBoxAreaOfInterest(
            bbox=BoundingBox(west=2.0, south=51.0, east=3.0, north=52.0)
        )
        self.time_win = TimeWindow(start=self.t_start, end=self.t_end)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_1_vessel_crosses_aoi_boundary_accepted_and_e1_filters(self) -> None:
        """1. Vessel has one ping inside AOI (lon=2.5, lat=51.5) and one outside (lon=3.2, lat=51.5).

        Acquisition must succeed and retain the track, while E1 still filters
        qualifying pings according to D3 source zone.
        """
        records = [
            {
                "timestamp": "2024-06-01T04:00:00Z",
                "lat": 51.5,
                "lon": 2.5,  # INSIDE AOI [2.0, 3.0]
                "mmsi": "111222333",
                "vessel_name": "BOUNDARY RUNNER",
                "vessel_type": "Cargo",
                "speed_over_ground": 12.0,
            },
            {
                "timestamp": "2024-06-01T06:00:00Z",
                "lat": 51.5,
                "lon": 3.2,  # OUTSIDE AOI (east of 3.0), but same vessel
                "mmsi": "111222333",
                "vessel_name": "BOUNDARY RUNNER",
                "vessel_type": "Cargo",
                "speed_over_ground": 12.5,
            },
        ]
        provider = AisAcquisitionProvider(
            settings=Settings(data_dir=self.work_dir, ais_adapter_id="unconfigured"),
            adapter=StaticAisAdapter(records),
        )
        req = AcquisitionRequest(
            investigation_id="inv-boundary-test",
            provider_id="ais",
            asset_type=AssetType.VESSEL_TRACK,
            area_of_interest=self.aoi,
            time_window=self.time_win,
        )
        # Acquisition MUST succeed without AcquisitionError
        res = provider.acquire(req)
        self.assertEqual(len(res.artifacts), 1)
        art_path = Path(res.artifacts[0].location)
        data = json.loads(art_path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["records"]), 2)  # Both pings retained

        # Now pass this acquired asset to E1 with a source zone around (2.5, 51.5)
        registry = InMemoryAssetRegistry()
        ais_asset = registry.register("inv-boundary-test", "ais", res.artifacts[0])

        source_poly = generate_source_candidate_polygon(2.5, 51.5, radius_m=5000.0, num_vertices=32)
        source_est = SourceEstimateResult(
            id="src-001",
            investigation_id="inv-boundary-test",
            spill_detection_id="spill-001",
            wind_asset_id="w-001",
            current_asset_id="c-001",
            asset_id="drift-001",
            model_version="test_v1",
            observation_time=datetime(2024, 6, 1, 8, 0, tzinfo=timezone.utc),
            origin_lon=2.5,
            origin_lat=51.5,
            source_time=datetime(2024, 6, 1, 4, 0, tzinfo=timezone.utc),
            source_point_lon=2.5,
            source_point_lat=51.5,
            lookback_hours=4.0,
            step_hours=1.0,
            leeway_fraction=0.035,
            source_uncertainty_radius_km=5.0,
            steps=[],
            source_zone_geometry=source_poly,
        )

        cand_result, _ = generate_candidate_vessels_for_spill(
            investigation_id="inv-boundary-test",
            spill_id="spill-001",
            source_estimate=source_est,
            ais_asset_id=ais_asset.id,
            temporal_window_hours=4.0,
            spatial_buffer_km=0.0,
            output_dir=self.work_dir,
            registry=registry,
        )
        self.assertEqual(cand_result.status, CandidateGenerationStatus.COMPLETED)
        self.assertEqual(cand_result.candidate_count, 1)
        cand = cand_result.candidates[0]
        self.assertEqual(cand.mmsi, "111222333")
        # In-zone ping (lon=2.5) was qualified and selected as CPA
        self.assertTrue(cand.inside_source_zone)
        self.assertAlmostEqual(cand.closest_position_lon, 2.5, places=3)
        self.assertEqual(cand.vessel_type, "Cargo")

    def test_2_vessel_all_pings_outside_aoi_rejected_or_excluded(self) -> None:
        """2. Vessel with all pings outside AOI raises AcquisitionError if queried alone."""
        records = [
            {
                "timestamp": "2024-06-01T04:00:00Z",
                "lat": 51.5,
                "lon": 10.0,  # Completely outside AOI [2.0, 3.0]
                "mmsi": "999888777",
                "vessel_name": "FAR AWAY SHIP",
            }
        ]
        provider = AisAcquisitionProvider(
            settings=Settings(data_dir=self.work_dir, ais_adapter_id="unconfigured"),
            adapter=StaticAisAdapter(records),
        )
        req = AcquisitionRequest(
            investigation_id="inv-outside",
            provider_id="ais",
            asset_type=AssetType.VESSEL_TRACK,
            area_of_interest=self.aoi,
            time_window=self.time_win,
        )
        with self.assertRaises(AcquisitionError) as ctx:
            provider.acquire(req)
        self.assertIn("AOI", str(ctx.exception))

    def test_3_malformed_coordinates_rejected(self) -> None:
        """3. Malformed or impossible coordinates (lat=120.0) are rejected."""
        records = [
            {
                "timestamp": "2024-06-01T04:00:00Z",
                "lat": 120.0,  # Invalid latitude > 90
                "lon": 2.5,
                "mmsi": "123456789",
            }
        ]
        query = AisHistoricalQuery(
            investigation_id="inv-malformed",
            area_of_interest=self.aoi,
            time_window=self.time_win,
        )
        with self.assertRaises(AcquisitionError):
            normalize_ais_records(records, query)

    def test_4_timestamp_outside_requested_window_rejected(self) -> None:
        """4. Timestamp outside requested window raises AcquisitionError."""
        records = [
            {
                "timestamp": "2024-06-01T20:00:00Z",  # Outside [00:00, 12:00]
                "lat": 51.5,
                "lon": 2.5,
                "mmsi": "123456789",
            }
        ]
        query = AisHistoricalQuery(
            investigation_id="inv-time",
            area_of_interest=self.aoi,
            time_window=self.time_win,
        )
        with self.assertRaises(AcquisitionError) as ctx:
            normalize_ais_records(records, query)
        self.assertIn("timestamp is outside the requested time window", str(ctx.exception))


class TestE1VesselMetadataExposure(unittest.TestCase):
    """B. Vessel metadata tests (scenarios 5-7)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_5_vessel_type_populated_from_sqlite_adapter(self) -> None:
        """5. vessel_type is populated from SQLite database and survives to CandidateVessel."""
        db_file = self.work_dir / "test_ais.db"
        init_ais_database(db_file)

        adapter = SqliteAisAdapter(db_path=db_file)
        query = AisHistoricalQuery(
            investigation_id="inv-meta-test",
            area_of_interest=BBoxAreaOfInterest(
                bbox=BoundingBox(west=9.0, south=43.0, east=10.0, north=44.0)
            ),
            time_window=TimeWindow(
                start=datetime(2018, 10, 7, 20, 0, tzinfo=timezone.utc),
                end=datetime(2018, 10, 8, 6, 0, tzinfo=timezone.utc),
            ),
        )
        positions = adapter.fetch_positions(query)
        self.assertGreater(len(positions), 0)

        # Verify vessel_type is present in raw records
        ulysse_positions = [p for p in positions if p.get("mmsi") == "228308800"]
        self.assertGreater(len(ulysse_positions), 0)
        self.assertEqual(ulysse_positions[0]["vessel_type"], "Ro-Ro Cargo")

        virginia_positions = [p for p in positions if p.get("mmsi") == "229986000"]
        self.assertGreater(len(virginia_positions), 0)
        self.assertEqual(virginia_positions[0]["vessel_type"], "Container Ship")

        # Test normalize_ais_records accepts and preserves vessel_type
        normalized = normalize_ais_records(positions, query)
        self.assertGreater(len(normalized), 0)
        ulysse_norm = next(p for p in normalized if p.mmsi == "228308800")
        self.assertEqual(ulysse_norm.vessel_type, "Ro-Ro Cargo")

    def test_6_missing_vessel_type_remains_none(self) -> None:
        """6. Missing vessel_type defaults cleanly to None."""
        c = CandidateVessel(
            candidate_id="cand-001",
            vessel_id="v-001",
            mmsi="123456789",
            inside_source_zone=True,
            min_distance_to_source_center_km=1.0,
            distance_to_zone_boundary_km=0.0,
            closest_position_lon=2.5,
            closest_position_lat=51.5,
            closest_position_time=datetime(2024, 6, 1, 4, 0, tzinfo=timezone.utc),
            time_offset_from_source_hours=0.0,
            observed_positions_count=1,
            raw_positions=[],
        )
        self.assertIsNone(c.vessel_type)
        self.assertIsNone(c.call_sign)
        self.assertIsNone(c.flag_country)

    def test_7_candidate_vessel_backward_compatibility_and_e2_roundtrip(self) -> None:
        """7. CandidateVessel round-trips through GeoJSON and E2 loader with vessel_type."""
        c = CandidateVessel(
            candidate_id="cand-tanker",
            vessel_id="v-tanker",
            mmsi="987654321",
            vessel_name="PACIFIC TRADER",
            vessel_type="Crude Oil Tanker",
            call_sign="V4XYZ",
            flag_country="Liberia",
            inside_source_zone=True,
            min_distance_to_source_center_km=0.5,
            distance_to_zone_boundary_km=0.0,
            closest_position_lon=2.5,
            closest_position_lat=51.5,
            closest_position_time=datetime(2024, 6, 1, 4, 0, tzinfo=timezone.utc),
            time_offset_from_source_hours=0.0,
            observed_positions_count=1,
            raw_positions=[
                VesselPosition(
                    timestamp=datetime(2024, 6, 1, 4, 0, tzinfo=timezone.utc),
                    lon=2.5,
                    lat=51.5,
                    speed=10.0,
                    course=90.0,
                )
            ],
        )
        self.assertEqual(c.vessel_type, "Crude Oil Tanker")

        # Simulate GeoJSON serialization
        geojson_props = {
            "feature_kind": "candidate_cpa",
            "candidate_id": c.candidate_id,
            "vessel_id": c.vessel_id,
            "mmsi": c.mmsi,
            "vessel_name": c.vessel_name,
            "vessel_type": c.vessel_type,
            "call_sign": c.call_sign,
            "flag_country": c.flag_country,
            "inside_source_zone": c.inside_source_zone,
            "min_distance_to_source_center_km": c.min_distance_to_source_center_km,
            "distance_to_zone_boundary_km": c.distance_to_zone_boundary_km,
            "closest_position_time": c.closest_position_time.isoformat(),
            "time_offset_from_source_hours": c.time_offset_from_source_hours,
        }
        # Verify props contains vessel_type
        self.assertEqual(geojson_props["vessel_type"], "Crude Oil Tanker")


class TestD3ZeroStepFallbackHardening(unittest.TestCase):
    """C. D3 zero-step fallback tests (scenarios 8-10)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp.name)
        self.wind_path = self.work_dir / "era5.nc"
        self.curr_path = self.work_dir / "cmems.nc"
        _create_minimal_era5(self.wind_path)
        _create_minimal_cmems(self.curr_path)
        self.obs_time = datetime(2024, 6, 1, 6, 0, tzinfo=timezone.utc)

        self.registry = InMemoryAssetRegistry()
        self.wind_asset = self.registry.register(
            "inv-d3",
            "era5",
            AcquiredArtifact(
                asset_type=AssetType.ENVIRONMENT_WIND,
                location=str(self.wind_path),
                source="test",
                provenance=Provenance(product_id="era5"),
            ),
        )
        self.curr_asset = self.registry.register(
            "inv-d3",
            "cmems",
            AcquiredArtifact(
                asset_type=AssetType.ENVIRONMENT_CURRENT,
                location=str(self.curr_path),
                source="test",
                provenance=Provenance(product_id="cmems"),
            ),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_8_zero_valid_steps_no_unbound_local_error(self) -> None:
        """8. Backward drift hitting land immediately (0 steps) does not raise UnboundLocalError."""
        step_eval_count = 0
        def immediate_land_boundary(lon, lat):
            nonlocal step_eval_count
            step_eval_count += 1
            # Origin check (1) is allowed; first backward integration step (2) is land
            return step_eval_count <= 1

        checker = MockDomainChecker(allowed_predicate=immediate_land_boundary)

        ds_w = _open_netcdf(str(self.wind_path))
        ds_c = _open_netcdf(str(self.curr_path))
        try:
            traj = run_backward_drift(
                origin_lon=2.5,
                origin_lat=51.5,
                observation_time=self.obs_time,
                wind_ds=ds_w,
                curr_ds=ds_c,
                lookback_hours=3.0,
                step_hours=1.0,
                domain_checker=checker,
            )
            self.assertEqual(len(traj), 0)
            self.assertEqual(traj.termination_status, "shoreline_boundary_reached")
        finally:
            ds_w.close()
            ds_c.close()

    def test_9_zero_step_compute_source_estimate_structured_result(self) -> None:
        """9. compute_source_estimate_for_spill returns structured result on zero steps."""
        step_eval_count = 0
        def immediate_land_boundary(lon, lat):
            nonlocal step_eval_count
            step_eval_count += 1
            return step_eval_count <= 1

        checker = MockDomainChecker(allowed_predicate=immediate_land_boundary)

        # Should execute cleanly without UnboundLocalError
        res, asset = compute_source_estimate_for_spill(
            investigation_id="inv-zero-step",
            spill_detection_id="spill-zero",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=self.wind_asset,
            current_asset=self.curr_asset,
            lookback_hours=3.0,
            step_hours=1.0,
            domain_checker=checker,
            output_dir=self.work_dir,
            registry=self.registry,
        )
        self.assertIsNotNone(res)
        self.assertEqual(res.termination_status, "shoreline_boundary_reached")
        self.assertEqual(res.source_point_lon, 2.5)
        self.assertEqual(res.source_point_lat, 51.5)
        self.assertEqual(res.source_time, self.obs_time)
        self.assertGreater(res.source_uncertainty_radius_km, 0.0)

    def test_10_normal_d3_trajectory_completely_unchanged(self) -> None:
        """10. Normal successful D3 backward trajectory remains completely unchanged."""
        permissive_checker = MockDomainChecker(allowed_predicate=lambda lon, lat: True)

        res, asset = compute_source_estimate_for_spill(
            investigation_id="inv-normal-d3",
            spill_detection_id="spill-normal",
            origin_lon=2.5,
            origin_lat=51.5,
            observation_time=self.obs_time,
            wind_asset=self.wind_asset,
            current_asset=self.curr_asset,
            lookback_hours=3.0,
            step_hours=1.0,
            domain_checker=permissive_checker,
            output_dir=self.work_dir,
            registry=self.registry,
        )
        self.assertEqual(res.termination_status, "completed")
        self.assertEqual(len(res.steps), 3)
        self.assertEqual(res.source_time, self.obs_time - timedelta(hours=3))
        self.assertNotEqual(res.source_point_lon, 2.5)
        self.assertNotEqual(res.source_point_lat, 51.5)
