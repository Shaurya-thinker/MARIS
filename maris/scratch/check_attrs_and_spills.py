import sys
sys.path.insert(0, 'backend')
import xarray as xr
import json

# Check ERA5 attributes
era5_path = r'backend/data/acquisitions/real-experiment/era5/20181007T172822Z_20181008T052822Z_44.2400_8.0000_40.5000_10.6000/era5_10m_wind.nc'
cmems_path = r'backend/data/acquisitions/real-experiment/cmems/20181007T172822Z_20181008T052822Z_44.2400_8.0000_40.5000_10.6000/cmems_surface_currents.nc'

w_ds = xr.open_dataset(era5_path)
c_ds = xr.open_dataset(cmems_path)

print("=== ERA5 ATTRIBUTES ===")
print("u10:", w_ds['u10'].attrs)
print("v10:", w_ds['v10'].attrs)

print("\n=== CMEMS ATTRIBUTES ===")
print("uo:", c_ds['uo'].attrs)
print("vo:", c_ds['vo'].attrs)

# Also let's check what B3 spill detection exists for the Corsica benchmark
from pathlib import Path
b3_spills = list(Path('backend/data').glob('**/spill*.geojson'))
print(f"\nFound {len(b3_spills)} spill geojson files:")
for sp in b3_spills:
    print(" -", sp)
    try:
        data = json.loads(sp.read_text())
        features = data.get('features', [])
        for f in features[:2]:
            props = f.get('properties', {})
            geom = f.get('geometry', {})
            print("    Props:", props.get('id') or props.get('name'), props.get('centroid'))
    except Exception as e:
        print("    Error:", e)
