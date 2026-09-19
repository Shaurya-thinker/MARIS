"""MARIS Real-Data Experiment sub-package.

Provides the real-data attribution experiment workflow, completely isolated
from the simulation showcase (src/simulation/) and the historical demo
(Corsica 2018).  This package MUST NOT import anything from the
simulation/ directory or from the existing G1 investigation workflow.

Services in this package:
    sentinel_discovery   — query CDSE for Sentinel-1 products without downloading them
    environment_selector — acquire ERA5 wind and CMEMS current data for a scene
    ais_search           — discover AIS vessel tracks near a reconstructed source zone
    experiment_runner    — orchestrate backward drift + vessel scoring for real inputs
    experiment_store     — SQLite-backed persistence of experiment runs and results
"""
