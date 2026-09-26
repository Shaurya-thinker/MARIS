import sqlite3, json

conn = sqlite3.connect('backend/data/real_experiments.db')
c = conn.cursor()
row = c.execute("SELECT run_id, backward_steps, vessels_json, source_lat, source_lon, era5_path, cmems_path FROM experiment_runs WHERE run_id='36612a3e-5982-4322-9048-b6fa8bba49ce'").fetchone()
if row:
    steps = json.loads(row[1])
    print('Run 36612a3e:')
    print('  Step count:', len(steps))
    for s in steps:
        print('   ', s)
    vessels = json.loads(row[2])
    print('  Vessels:', vessels)
    print('  Source:', row[3], row[4])
    print('  ERA5:', row[5])
    print('  CMEMS:', row[6])
