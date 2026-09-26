import sys
sys.path.insert(0, 'backend')
from datetime import datetime, timezone

from app.services.real_experiment.sentinel_discovery import SentinelDiscoveryService
from app.core.config import settings

svc = SentinelDiscoveryService(cfg=settings)
products = svc.discover(
    west=8.0, south=41.0, east=10.6, north=44.5,
    start=datetime(2018, 10, 8, 5, 27, 0, tzinfo=timezone.utc),
    end=datetime(2018, 10, 8, 5, 29, 0, tzinfo=timezone.utc),
    limit=20,
)

print("Target points:")
print("  Collision: 43.035 N, 9.425 E")
print("  Spill obs: 43.248 N, 9.478 E")
print("\nChecking discovered products:")

for p in products:
    fp = p.footprint
    coords = fp.get('coordinates', [[]])[0] if fp else []
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    min_lat, max_lat = (min(lats), max(lats)) if lats else (None, None)
    min_lon, max_lon = (min(lons), max(lons)) if lons else (None, None)
    
    in_collision = (min_lat <= 43.035 <= max_lat and min_lon <= 9.425 <= max_lon) if min_lat else False
    in_spill = (min_lat <= 43.248 <= max_lat and min_lon <= 9.478 <= max_lon) if min_lat else False
    
    print(f"\nTitle: {p.title}")
    print(f"  ID: {p.product_id}")
    print(f"  Mode/Class: {p.mode} / {p.product_class}")
    print(f"  Centroid: lat={p.centroid_lat}, lon={p.centroid_lon}")
    if min_lat:
        print(f"  Bbox: west={min_lon:.2f}, south={min_lat:.2f}, east={max_lon:.2f}, north={max_lat:.2f}")
    print(f"  Bbox contains collision (43.035N, 9.425E): {in_collision}")
    print(f"  Bbox contains spill (43.248N, 9.478E): {in_spill}")

