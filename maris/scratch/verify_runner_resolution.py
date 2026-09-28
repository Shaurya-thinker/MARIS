import sys
sys.path.insert(0, 'backend')
from datetime import datetime, timezone
from app.services.real_experiment.experiment_runner import ExperimentRunner

era5_path = r'backend/data/acquisitions/real-experiment/era5/20181007T172822Z_20181008T052822Z_44.2400_8.0000_40.5000_10.6000/era5_10m_wind.nc'
cmems_path = r'backend/data/acquisitions/real-experiment/cmems/20181007T172822Z_20181008T052822Z_44.2400_8.0000_40.5000_10.6000/cmems_surface_currents.nc'
obs_time = datetime(2018, 10, 8, 5, 28, 22, tzinfo=timezone.utc)

runner = ExperimentRunner()

vessels = [
    {"mmsi": "228308800", "vessel_name": "MV ULYSSE", "positions": []},
    {"mmsi": "247112233", "vessel_name": "MEDITERRANEAN STAR", "positions": []}
]

# Pass the exact prompt inputs: observation_lat = 41.9907, observation_lon = 9.7874
res = runner.run(
    satellite_product_id="S1A_IW_GRDH_1SDV_20181008T052807_20181008T052832_024040_02A081_04F2",
    observation_lon=9.7874,
    observation_lat=41.9907,
    observation_time=obs_time,
    era5_netcdf_path=era5_path,
    cmems_netcdf_path=cmems_path,
    backtrack_hours=12.0,
    step_hours=1.0,
    selected_vessels=vessels,
)

print("=== VERIFY EXPERIMENT RUNNER RESOLUTION ===")
print(f"Reconstructed Source: {res.source_lat:.4f}°N, {res.source_lon:.4f}°E")
print(f"Uncertainty Radius: {res.source_radius_m:.1f} m")
print(f"Step count: {len(res.backward_steps)}")
for v in res.vessels:
    print(f"\nVessel: {v.vessel_name} (MMSI: {v.mmsi})")
    print(f"  Min Source Distance: {v.min_source_distance_km:.2f} km")
    print(f"  Temporal Overlap: {v.temporal_overlap_hours:.2f} h")
    print(f"  Trajectory Overlap: {v.trajectory_overlap_fraction*100:.1f} %")
    print(f"  Heading Consistency: {v.heading_consistency*100:.1f} %")
    print(f"  Speed Consistency: {v.speed_consistency*100:.1f} %")
    print(f"  AIS Positions: {v.ais_position_count}")
    print(f"  Evidence Consistency Score (ECS): {v.evidence_consistency_score*100:.2f} % (Rank {v.rank})")
