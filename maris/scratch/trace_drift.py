import sys
sys.path.insert(0, 'backend')
from datetime import datetime, timezone
from app.services.drift_modelling import _open_netcdf
from app.services.source_estimation import run_backward_drift

era5_path = r'backend/data/acquisitions/real-experiment/era5/20181007T172822Z_20181008T052822Z_44.2400_8.0000_40.5000_10.6000/era5_10m_wind.nc'
cmems_path = r'backend/data/acquisitions/real-experiment/cmems/20181007T172822Z_20181008T052822Z_44.2400_8.0000_40.5000_10.6000/cmems_surface_currents.nc'

wind_ds = _open_netcdf(era5_path)
curr_ds = _open_netcdf(cmems_path)

obs_time = datetime(2018, 10, 8, 5, 28, 22, tzinfo=timezone.utc)

print('=== TEST 1: Origin at 41.9907N, 9.7874E (Scene Centroid) ===')
traj1 = run_backward_drift(
    origin_lon=9.7874,
    origin_lat=41.9907,
    observation_time=obs_time,
    wind_ds=wind_ds,
    curr_ds=curr_ds,
    lookback_hours=12.0,
    step_hours=1.0,
)
print('Traj1 termination:', traj1.termination_status, 'reason:', traj1.termination_reason)
print('Step count:', len(traj1))
for i, s in enumerate(traj1):
    t_str = s.timestamp.strftime('%Y-%m-%d %H:%M')
    print(f"Step {i:02d}: t={t_str} | lat={s.lat:.4f}, lon={s.lon:.4f} | wind=({s.u_wind_ms:+.2f}, {s.v_wind_ms:+.2f}) | curr=({s.u_current_ms}, {s.v_current_ms}) | drift=({s.drift_u_ms:+.3f}, {s.drift_v_ms:+.3f}) | dist={s.cumulative_backward_distance_m:.0f}m")

print('\n=== TEST 2: Origin at 43.25N, 9.48E (Cap Corse Collision / Spill Site) ===')
traj2 = run_backward_drift(
    origin_lon=9.48,
    origin_lat=43.25,
    observation_time=obs_time,
    wind_ds=wind_ds,
    curr_ds=curr_ds,
    lookback_hours=12.0,
    step_hours=1.0,
)
print('Traj2 termination:', traj2.termination_status, 'reason:', traj2.termination_reason)
print('Step count:', len(traj2))
for i, s in enumerate(traj2):
    t_str = s.timestamp.strftime('%Y-%m-%d %H:%M')
    print(f"Step {i:02d}: t={t_str} | lat={s.lat:.4f}, lon={s.lon:.4f} | wind=({s.u_wind_ms:+.2f}, {s.v_wind_ms:+.2f}) | curr=({s.u_current_ms}, {s.v_current_ms}) | drift=({s.drift_u_ms:+.3f}, {s.drift_v_ms:+.3f}) | dist={s.cumulative_backward_distance_m:.0f}m")
