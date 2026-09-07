# MARIS Backend

This is the MARIS backend foundation.

The current purpose is to provide the initial API foundation, domain contracts,
and a data-acquisition framework. The HTTP surface is still a health endpoint at `GET /health`. Sentinel-1, ERA5,
CMEMS, and AIS acquisition are library providers, not API routes. The AIS
provider is an adapter boundary only; no live AIS service is configured.

## Run locally

From this directory, install the dependencies and start the development server:

```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

The health endpoint is available at `http://127.0.0.1:8000/health`.

## Tests

From this directory:

```bash
python -m unittest discover -s tests -v
```

Tests mock CDSE, CDS, Copernicus Marine, and AIS adapter I/O. They do not require
Copernicus or AIS credentials and they do not download Sentinel-1 products, ERA5
NetCDF, CMEMS NetCDF, or live AIS data.

## Sentinel-1 / CDSE configuration

The Sentinel-1 provider queries the Copernicus Data Space Ecosystem OData
catalogue and downloads the selected product with the official zipper endpoint.

Set credentials in the environment. Do not put secrets in source files or `.env`
files that are committed to Git.

Required for download (one of):

| Variable | Purpose |
|---|---|
| `CDSE_USERNAME` and `CDSE_PASSWORD` | CDSE account used to request an access token |
| `CDSE_ACCESS_TOKEN` | Pre-issued bearer token; skips the password grant |

Optional:

| Variable | Default | Purpose |
|---|---|---|
| `CDSE_TOTP` | empty | 2FA code when the account requires it |
| `CDSE_CLIENT_ID` | `cdse-public` | OAuth client id |
| `CDSE_TOKEN_URL` | CDSE Keycloak token endpoint | Token grant URL |
| `CDSE_CATALOGUE_URL` | `https://catalogue.dataspace.copernicus.eu/odata/v1` | OData catalogue |
| `CDSE_DOWNLOAD_URL` | `https://zipper.dataspace.copernicus.eu/odata/v1` | Product download |
| `MARIS_DATA_DIR` | `backend/data` | Local artifact root (not `public/`) |

Downloaded products are stored under `{MARIS_DATA_DIR}/acquisitions/{investigation_id}/sentinel1/{product_id}/`. That directory is gitignored.

## ERA5 / CDS configuration

The ERA5 provider requests hourly 10 m wind from the Copernicus Climate Data
Store using the official `cdsapi` client. It does not use CDSE credentials.

Dataset: `reanalysis-era5-single-levels` (ERA5 hourly data on single levels).

Variables requested:

- `10m_u_component_of_wind`
- `10m_v_component_of_wind`

Output: NetCDF (`data_format=netcdf`), clipped to the investigation bounding box
(`area` as north, west, south, east). The provider does not request a global
grid.

Required:

| Variable | Purpose |
|---|---|
| `CDSAPI_KEY` | CDS personal access token from https://cds.climate.copernicus.eu/how-to-api |

Optional:

| Variable | Default | Purpose |
|---|---|---|
| `CDSAPI_URL` | `https://cds.climate.copernicus.eu/api` | CDS API base URL |

Accept the ERA5 dataset licence on the CDS website before live downloads will
succeed. Do not put `CDSAPI_KEY` in source files or committed `.env` files.

NetCDF artifacts are stored under
`{MARIS_DATA_DIR}/acquisitions/{investigation_id}/era5/{request-key}/era5_10m_wind.nc`.
That directory is gitignored.

## CMEMS / Copernicus Marine configuration

The CMEMS provider subsets near-surface ocean currents from the Copernicus
Marine Service using the official `copernicusmarine` toolbox. It does not use
CDSE or CDS credentials.

Product: `GLOBAL_MULTIYEAR_PHY_001_030` (GLORYS12V1 global ocean physics reanalysis).

Dataset: `cmems_mod_glo_phy_my_0.083deg_P1D-m` (daily means).

Variables requested:

- `uo` — eastward sea-water velocity
- `vo` — northward sea-water velocity

Near-surface only: the subset uses depth 0–1 m with nearest-level selection so
the toolbox returns the shallowest GLORYS12V1 standard level (documented first
z-level 0.494025 m in the product NetCDF). A full water-column profile is not
requested. Output is NetCDF, clipped to the investigation bounding box.

Required for live acquisition:

| Variable | Purpose |
|---|---|
| `COPERNICUSMARINE_SERVICE_USERNAME` | Copernicus Marine username |
| `COPERNICUSMARINE_SERVICE_PASSWORD` | Copernicus Marine password |

Aliases: `CMEMS_USERNAME` and `CMEMS_PASSWORD`.

Accept the product licence on the Copernicus Marine website before live
downloads will succeed. Do not put Marine Service credentials in source files
or committed `.env` files.

NetCDF artifacts are stored under
`{MARIS_DATA_DIR}/acquisitions/{investigation_id}/cmems/{request-key}/cmems_surface_currents.nc`.
That directory is gitignored.

## AIS / historical vessel positions (Stage A3.5)

The AIS provider acquires **historical** vessel positions for an investigation
AOI and time window. It is not a live vessel lookup and it is not wired to a
commercial AIS vendor.

Architecture:

```text
AcquisitionRequest (VESSEL_TRACK)
        ↓
AisAcquisitionProvider
        ↓
AisSourceAdapter (injectable)
        ↓
normalized position records
        ↓
JSON artifact + AcquiredArtifact
```

No live AIS provider is configured. `UnconfiguredAisAdapter` is the default and
raises a configuration error. A real historical adapter can be injected later
without changing `AcquisitionProvider` or A2 models.

Normalized observation fields (null when the source omits them):

- timestamp, lat, lon
- MMSI, IMO, vessel name
- speed over ground, course over ground, heading
- navigation status

MARIS does not invent, interpolate, or reconstruct trajectories in this stage.

Optional configuration placeholder (not a credential, not required):

| Variable | Default | Purpose |
|---|---|---|
| `MARIS_AIS_ADAPTER` | `unconfigured` | Name of a future historical AIS adapter |

Do not set CDSE, CDS, or CMEMS credentials for AIS. Future AIS secrets, if any,
will be AIS-specific and are not defined yet.

JSON artifacts are stored under
`{MARIS_DATA_DIR}/acquisitions/{investigation_id}/ais/{request-key}/ais_positions.json`.
That directory is gitignored. MARIS does not currently have live AIS coverage.

## Scientific Data Validation (Stage A4)

Validation runs after acquisition. It is a separate, read-only step that never
modifies, moves, or deletes an acquired artifact.

### A4.1 — Generic Artifact Validation

The validation framework lives in `app/validation/`. It is provider-independent:
it knows nothing about Sentinel-1, ERA5, CMEMS, AIS, NetCDF, GeoTIFF, or any
other format.

Architecture:

```text
AcquiredArtifact
      ↓
ScientificValidator.validate(artifact)
      ↓
ValidationResult
      ↓
INFO / WARNING / ERROR issues
```

`GenericArtifactValidator` performs checks applicable to every artifact:

- artifact location is a non-empty string
- artifact path exists on the filesystem
- artifact is accessible (file or directory)
- artifact is not empty (size > 0)
- provider/source field is present
- provenance object is available

Severity rules:

- `ERROR` — artifact is not valid for downstream scientific processing;
  `ValidationResult.passed` is `False`
- `WARNING` — artifact may proceed but the limitation must remain visible;
  does not affect `passed`
- `INFO` — informational only; does not affect `passed`

All checks are independent: a location failure does not suppress a source check.
Fail-closed: an unexpected error during a check produces an `ERROR` issue, not
a silent downgrade to `WARNING`.

Validation provenance is recorded in every `ValidationResult`:

- `validator_name` and `validator_version`
- `validated_at` (UTC timestamp)

No credentials or secrets are included in the result.

Provider-specific scientific validation (NetCDF variable checks, coordinate
ranges, SAR metadata, AIS record integrity) is implemented in A4.2–A4.5 as
subclasses of `ScientificValidator`. A4.1 does not inspect file contents.

### A4.2 — Sentinel-1 Product Validation

`Sentinel1Validator` (`app/validation/sentinel1.py`) validates a Sentinel-1
ZIP/SAFE artifact acquired by the A3.2 provider. It uses stdlib `zipfile` and
`xml.etree.ElementTree` only — no GDAL, rasterio, xarray, numpy, or SNAP.

Checks performed:

- ZIP can be opened and passes CRC integrity test
- `manifest.safe` is present and is valid XML
- Platform `familyName` is `SENTINEL-1`; instrument abbreviation is `SAR`
- `productType` is a recognised Sentinel-1 type (`GRD`, `SLC`, `RAW`, `OCN`)
- Sensor mode is extracted; `IW` is preferred, other valid modes produce a WARNING
- Processing level `GRDH` is preferred; `GRDM` produces a WARNING
- Polarisation channels are present (missing → WARNING)
- Sensing start and stop times are parseable and start < stop
- Spatial footprint coordinates are in valid WGS84 ranges (missing → WARNING)
- Orbit number is present (missing → WARNING)

Validation classification stored in `ValidationResult.metadata`:

| Classification | Condition |
|---|---|
| `PREFERRED` | no ERRORs, no WARNINGs |
| `USABLE_WITH_WARNINGS` | no ERRORs, at least one WARNING |
| `INVALID` | at least one ERROR; `passed=False` |

This validator does not perform SAR pixel analysis, oil-spill detection,
drift modelling, or AOI intersection. It confirms that the product package
is structurally intact and identifies as a Sentinel-1 SAR acquisition.
Read-only: the source artifact is never modified.

### A4.3 — ERA5 Wind NetCDF Validation

`Era5Validator` (`app/validation/era5.py`) validates an ERA5 10 m wind NetCDF
artifact acquired by the A3.3 provider. It uses `xarray` with the `netcdf4`
engine. No GDAL, rasterio, SNAP, or drift libraries are used.

Wind variables inspected (as produced by CDS `reanalysis-era5-single-levels`):

- `u10` — 10 m eastward wind component
- `v10` — 10 m northward wind component

Checks performed:

- NetCDF can be opened (not corrupt or unreadable)
- `u10` and `v10` are present
- `time`, `latitude`, `longitude` coordinates are present and non-empty
- Latitude values are finite and within \[-90, 90\]; ascending and descending both accepted
- Longitude values are finite and within \[-180, 180\]; ascending and descending both accepted
- Non-monotonic latitude or longitude → WARNING
- Time is monotonically ordered (WARNING if not)
- Wind variable `units` attribute is an accepted m s⁻¹ representation (`m s**-1` or `m/s`)
- Missing `units` attribute → WARNING
- Unexpected units → ERROR
- ERA5 `_FillValue` is transparently exposed as NaN by xarray; all-NaN variable → ERROR
- Partial NaN/missing values → WARNING
- ±inf values → WARNING

Metadata recorded in `ValidationResult.metadata`:

- `identified_u_component`, `identified_v_component`
- `latitude_min`, `latitude_max`, `longitude_min`, `longitude_max`
- `time_start`, `time_end`
- `u_units`, `v_units`
- `u_missing_count`, `v_missing_count`, `u_nonfinite_count`, `v_nonfinite_count`
- `validation_classification`

Validation classification:

| Classification | Condition |
|---|---|
| `PREFERRED` | no ERRORs, no WARNINGs |
| `USABLE_WITH_WARNINGS` | no ERRORs, at least one WARNING |
| `INVALID` | at least one ERROR; `passed=False` |

A4.3 does **not** perform drift modelling, interpolation, resampling, or AOI
intersection. It validates that the ERA5 wind field is structurally sound and
scientifically interpretable. Read-only: the source NetCDF is never modified.

### A4.4 — CMEMS Near-Surface Current NetCDF Validation

`CmemsValidator` (`app/validation/cmems.py`) validates a CMEMS near-surface
ocean current NetCDF artifact acquired by the A3.4 provider. It uses `xarray`
with the `netcdf4` engine. No GDAL, rasterio, scipy, or drift libraries are
used. Read-only: the source NetCDF is never modified.

Current variables inspected (as produced by GLORYS12V1 / A3.4):

- `uo` — eastward sea-water velocity
- `vo` — northward sea-water velocity

Retained depth dimension:

The A3.4 provider selects the nearest level to depth 0–1 m, which returns the
first GLORYS12V1 standard level (~0.494025 m). The depth dimension is
**retained** in the output (size 1, not squeezed). A4.4 verifies that the
depth dimension is present and records it. No vertical interpolation is
performed.

Near-surface depth validation:

A key A4.4 check. The validator checks whether the minimum depth value in the
artifact matches the expected first GLORYS12V1 level (0.494025 m) within a
small tolerance. A mismatch produces a **WARNING** (not an ERROR), because the
artifact may still be scientifically usable; it indicates the A3.4
near-surface contract may not be fully satisfied.

Checks performed:

- NetCDF can be opened (not corrupt or unreadable)
- `uo` and `vo` are present
- `time`, `latitude`, `longitude`, `depth` coordinates are present and non-empty
- Depth values are positive, finite, in metres; positive direction is `down`
- Depth ≈ 0.494025 m (first GLORYS12V1 level) within 0.01 m tolerance (WARNING if not)
- Latitude values are finite and within \[-90, 90\]
- Longitude values are finite and within \[-180, 180\] (signed; not normalised)
- Latitude and longitude are monotonically ordered (WARNING if not)
- Time is monotonically ordered (WARNING if not)
- Current variable `units` attribute is an accepted m s⁻¹ representation
  (`m s-1`, `m/s`, or `m s**-1`)
- Missing `units` attribute → WARNING; unexpected units → ERROR
- Depth `units` must be metres or clearly equivalent; missing → WARNING; other → ERROR
- CMEMS `_FillValue` is transparently exposed as NaN by xarray; all-NaN variable → ERROR
- Partial NaN/missing values → WARNING
- ±inf values → WARNING
- Useful dataset-level global attributes are recorded

Metadata recorded in `ValidationResult.metadata`:

- `identified_u_component`, `identified_v_component`
- `latitude_min`, `latitude_max`, `latitude_count`, `latitude_order`
- `longitude_min`, `longitude_max`, `longitude_count`, `longitude_order`
- `depth_min`, `depth_max`, `depth_count`, `depth_units`, `depth_positive`
- `near_surface_depth`, `near_surface_depth_recognized`
- `time_start`, `time_end`, `time_count`
- `u_units`, `v_units`
- `u_missing_count`, `v_missing_count`, `u_nonfinite_count`, `v_nonfinite_count`
- `u_dims`, `v_dims`, `u_shape`, `v_shape`
- `dataset_product_id`, `dataset_dataset_id`, `dataset_source`, `dataset_institution`,
  `dataset_processing_level`, `dataset_conventions`
- `validation_classification`

Validation classification:

| Classification | Condition |
|---|---|
| `PREFERRED` | no ERRORs, no WARNINGs |
| `USABLE_WITH_WARNINGS` | no ERRORs, at least one WARNING |
| `INVALID` | at least one ERROR; `passed=False` |

A4.4 validates CMEMS current data.
It does **not** perform drift modelling, current interpolation, resampling,
spill detection, or attribution.

### A4.5 — AIS Position JSON Validation

`AisValidator` (`app/validation/ais.py`) validates a normalized historical AIS
position JSON artifact acquired by the A3.5 provider (`maris.ais.positions.v1`).
It uses Python standard library only (`json`, `datetime`, `math`, `pathlib`).
No external AIS API, database, queue, or drift libraries are used. Read-only:
the source artifact is never modified.

Required position fields:

- `timestamp` — parseable ISO datetime
- `lat` — numeric and finite, within \[-90, +90\]
- `lon` — numeric and finite, within \[-180, +180\] (signed WGS-84)

Optional identity and navigation fields:

- `mmsi` — when present, must be 9 numeric digits (missing → WARNING; invalid → ERROR)
- `imo` — when present, must be 7 numeric digits (missing → WARNING; invalid → ERROR)
- `vessel_name` — when present, must be non-empty string (missing/empty → WARNING)
- `sog` / `speed_over_ground` — when present, must be finite and non-negative (>= 0)
- `cog` / `course_over_ground` — when present, must be finite and within \[0, 360\)
- `heading` — when present, must be finite and within \[0, 360\)

Checks performed:

- JSON artifact exists, is accessible, and non-empty
- JSON structure is valid object with expected schema identifier if present
- Position records collection exists and is a non-empty list of objects
- Required position fields (timestamp, lat, lon) exist and are valid on every record
- Lat/lon are within WGS-84 valid ranges; signed longitude convention
- Timestamps parseable; chronological order checked (WARNING if non-monotonic)
- Duplicate complete position records detected (WARNING if found)
- Duplicate timestamps detected (WARNING if found)
- MMSI (9 digits), IMO (7 digits), vessel name validation when present
- Motion fields (SOG, COG, heading) range validation when present
- Partial invalid records → ERROR (`AIS_PARTIAL_INVALID_RECORDS`)
- Empty or all-invalid records → ERROR (`AIS_ALL_RECORDS_INVALID` / `AIS_POSITIONS_EMPTY`)

Metadata recorded in `ValidationResult.metadata`:

- `artifact_schema`
- `position_count`, `valid_position_count`, `invalid_position_count`
- `latitude_min`, `latitude_max`, `longitude_min`, `longitude_max`
- `time_start`, `time_end`, `time_count`, `temporal_order`, `temporal_span_seconds`
- `duplicate_position_count`, `duplicate_timestamp_count`
- `records_with_mmsi`, `records_with_imo`, `records_with_vessel_name`
- `records_with_sog`, `records_with_cog`, `records_with_heading`
- `sog_min`, `sog_max`, `cog_min`, `cog_max`, `heading_min`, `heading_max`
- `validation_classification`

Validation classification:

| Classification | Condition |
|---|---|
| `PREFERRED` | no ERRORs, no WARNINGs |
| `USABLE_WITH_WARNINGS` | no ERRORs, at least one WARNING |
| `INVALID` | at least one ERROR; `passed=False` |

A4.5 validates normalized AIS observation data.
It does **not** perform AIS intelligence, vessel attribution, suspicious behavior
detection, trajectory reconstruction, position interpolation, drift modelling, or
frontend integration.

## Stage B1 — Real Sentinel-1 Scene Ingestion

Stage B1 orchestrates local Sentinel-1 artifact ingestion:

```text
Sentinel-1 ZIP
      ↓
A4.2 Sentinel-1 validation
      ↓
Asset registration (AssetRegistry)
      ↓
SatelliteScene creation
      ↓
API response
```

Service implementation: `app/services/sentinel1_ingestion.py`
Ingestion entry point: `ingest_sentinel1_artifact()`

Processes:
1. Validates the local Sentinel-1 ZIP file using `Sentinel1Validator` (A4.2).
2. Fails closed if validation fails (`passed=False`), preventing scene creation and asset registration.
3. On validation success, registers the artifact in `AssetRegistry` (`Asset`).
4. Converts A4.2 extracted GML footprint coordinates into a GeoJSON-style `PolygonAreaOfInterest` (`[longitude, latitude]` order).
5. Assembles and returns a `SatelliteScene` domain model along with the `ValidationResult`.

API Endpoint:

`POST /api/v1/investigations/{investigation_id}/scenes/sentinel1/ingest`

Payload:
```json
{
  "artifact_path": "/path/to/S1A_IW_GRDH_...zip"
}
```

Response:
```json
{
  "scene": { ... },
  "validation": { ... }
}
```

Stage B1 ingests **existing local Sentinel-1 ZIP artifacts**. It does **not** perform automatic CDSE provider acquisition (Stage C), SAR image preprocessing, AI/ML detection, or spill geometry extraction.

## Stage B2 — SAR Preprocessing & Scene Preparation

Stage B2 converts raw Sentinel-1 GRD ZIP artifacts into analysis-ready calibrated SAR GeoTIFF rasters ($\sigma^0$ in dB):

```text
Raw Sentinel-1 GRD (ZIP)
      ↓
Measurement raster reading (rasterio / zipfile)
      ↓
Radiometric LUT calibration (annotation/calibration/calibration-*.xml)
      ↓
sigma0 linear → sigma0 dB (10 * log10(sigma0))
      ↓
Invalid / zero-padding pixel masking (NaN / nodata)
      ↓
Geospatial GeoTIFF export (preserving CRS, transform, bounds)
      ↓
Derived Asset Registration (AssetRegistry)
```

Service implementation: `app/services/sentinel1_preprocessing.py`
Preprocessing entry point: `preprocess_sentinel1_scene()`

Processes:
1. Opens source Sentinel-1 ZIP in read-only mode (source file remains 100% byte-for-byte unchanged).
2. Reads 16-bit measurement GeoTIFF rasters and parses ESA Sentinel-1 calibration XML annotation LUTs (`<calibrationVector>`).
3. Applies radiometric calibration: $\sigma^0 = \frac{DN^2}{A_{\sigma}^2}$ and converts to decibels ($\sigma^0_{\text{dB}} = 10 \log_{10}(\sigma^0)$).
4. Handles invalid, zero, or non-finite pixels by masking them to `NaN` with `nodata=-9999.0`.
5. Preserves available polarizations (`VV`, `VH`) as separate raster bands without arbitrary channel mixing.
6. Preserves spatial georeferencing metadata (CRS, Affine transform, pixel resolution, image bounds).
7. Exports derived GeoTIFF raster to `{MARIS_DATA_DIR}/derived/{investigation_id}/sar/{scene_id}/sentinel1_sigma0_db.tif`.
8. Registers the derived raster in `AssetRegistry` as an `Asset` (`AssetType.IMAGERY_PREVIEW`).

*Real-data status note*: Stage B2 logic has been verified completely offline using controlled synthetic Sentinel-1 GRD test fixtures (`tests/test_sentinel1_preprocessing.py`). No real Sentinel-1 ZIP artifact has yet been processed locally. Stage B2 does **not** perform model-specific ML normalization, AI spill segmentation (Stage B3), drift modeling, or frontend integration.

## Stage B3 — Spill Detection & Geometry

Stage B3 performs automated detection of oil spill candidates and extraction of geospatial vector geometries from analysis-ready calibrated SAR GeoTIFF rasters ($\sigma^0$ in dB) produced in Stage B2:

```text
B2 sigma0 dB GeoTIFF
        ↓
Modular Spill Detector (BaseSpillDetector)
        ↓
Adaptive Local Background Thresholding (CFAR)
        ↓
spill probability / candidate mask
        ↓
noise / artefact-aware morphological cleanup
        ↓
connected spill regions & size filtering
        ↓
vector polygon extraction (rasterio.features.shapes)
        ↓
SpillDetection domain object
        ↓
derived artifacts + AssetRegistry + provenance
```

Service implementation: `app/services/spill_detection/service.py`
Detection entry point: `detect_spills_from_sar_scene()`

### Architecture & Modular Detector Interface
- `BaseSpillDetector`: Abstract base class defining the detection interface `detect(raster, valid_mask, polarization, pixel_size_m) -> DetectorResult`.
- `AdaptiveThresholdSpillDetector`: Deterministic baseline detector implementing CFAR-inspired adaptive local background thresholding.
- Model-agnostic design: Allows future deep-learning segmentation models (e.g. U-Net, DeepLabV3+, SAM) to be plugged in as drop-in replacements without modifying upstream SAR ingestion or downstream polygonization, asset registration, or provenance.

### Scientific Basis & Polarization Strategy
- **Wave Damping Mechanism**: Mineral oil films damp capillary and short gravity ocean waves (Bragg scattering), causing specular reflection away from the radar antenna and producing distinct dark patches (radar backscatter $\sigma^0$ typically $3.0$ to $10+$ dB below clean sea clutter).
- **Polarization Handling**: VV (Vertical-Vertical) polarization is prioritized as the primary channel due to its significantly higher ocean signal-to-clutter ratio (SCR) compared to cross-polarization (VH). VH cross-polarization is dominated by volume scattering and instrument noise floor (NESZ). VV and VH channels are kept strictly separate and never arbitrarily averaged or combined. Callers can explicitly select the polarization channel.
- **Adaptive Clutter Sampling**: Excludes deep dark formations from contaminating ambient sea statistics (avoiding slick self-suppression). Uses 2D running summed-area integral tables to compute local background mean $\mu_{bg}$ and standard deviation $\sigma_{bg}$.
- **Dual Criteria**: Detects candidate pixels where damping contrast $\Delta \sigma^0 = \mu_{bg} - \sigma^0 \ge \text{damping\_threshold\_db}$ (default: 3.5 dB) and statistical anomaly $\sigma^0 \le \mu_{bg} - k \cdot \sigma_{bg}$ (default: $k=2.0$).
- **Morphological Cleanup**: Applies 3x3 binary opening to suppress single-pixel speckle noise and enforces border margins away from nodata edges.
- **Connected-Region Extraction**: Groups contiguous candidate pixels, filters out sub-resolution speckle features below `min_area_m2` (default: 25,000 $\text{m}^2$) or `min_pixels`, and extracts closed GeoJSON vector polygons using `rasterio.features.shapes`.

### Output Artifacts & Domain Integration
Deterministic output paths under `{MARIS_DATA_DIR}/derived/{investigation_id}/sar/{scene_id}/`:
1. `spill_mask.tif` — Multi-band GeoTIFF:
   - Band 1: Binary candidate detection mask (uint8)
   - Band 2: Continuous damping confidence/probability in $[0.0, 1.0]$ (float32)
2. `spill_geometry.geojson` — GeoJSON FeatureCollection with per-region properties (area in $\text{m}^2/\text{km}^2$, damping contrast, bounding box, centroid, confidence).
3. `SpillDetection` domain object (`app.models.satellite.SpillDetection`):
   - `detected`: boolean
   - `confidence`: float $[0.0, 1.0]$
   - `area`: total area in $\text{m}^2$
   - `geometry`: GeoJSON Polygon / MultiPolygon (or empty GeometryCollection if not detected)
   - `metadata`: bounding box, centroid, raster statistics, detector parameters, region breakdown
4. `Asset` registration (`app.models.asset.Asset`):
   - Type: `AssetType.SPILL_GEOMETRY`
   - Provenance explicitly links back to parent B2 asset ID and Sentinel-1 scene ID.

### API Endpoint
`POST /api/v1/investigations/{investigation_id}/scenes/{scene_id}/spill-detect`

Payload:
```json
{
  "sar_asset_path": "/path/to/sentinel1_sigma0_db.tif",
  "damping_threshold_db": 3.5,
  "k_sigma": 2.0,
  "min_area_m2": 25000.0
}
```

### Scientific Limitations
- **Baseline Detector**: The adaptive threshold detector is an automated anomaly baseline, NOT a validated oil-spill classifier.
- **Look-alikes**: In SAR oceanography, low-backscatter dark spots are also formed by natural look-alikes:
  - Low-wind calm ocean zones ($< 2-3$ m/s)
  - Natural biogenic slicks (algal blooms, fish oils)
  - Atmospheric wind shadows, gravity waves, and rain cells
  - Oceanographic internal waves and upwelling
- **Attribution**: Stage B3 does NOT claim legal certainty or vessel attribution.

*Real-data status note*: Stage B3 logic has been verified offline using controlled synthetic Sentinel-1 calibrated GeoTIFF test fixtures (`tests/test_spill_detection.py`). No real Sentinel-1 GeoTIFF has yet been processed locally. Stage B3 does **not** implement drift modeling, AIS correlation, or frontend changes.

## Stage C1 — Automatic Environmental Acquisition

Stage C1 automatically derives the spatial and temporal environmental requirements for an investigation or Sentinel-1 scene context, acquires ERA5 wind and CMEMS surface-current data via the existing acquisition layer, rigorously validates the downloaded artifacts with A4 scientific validators, registers valid data in the `AssetRegistry`, and constructs `WindField` and `CurrentField` domain models:

```text
Investigation / Sentinel-1 scene context
        ↓
environmental spatial + temporal framing
        ↓
ERA5 wind acquisition + CMEMS surface-current acquisition
        ↓
A4 scientific validation (Era5Validator, CmemsValidator)
        ↓
AssetRegistry registration (validated artifacts only)
        ↓
WindField / CurrentField domain objects
```

Service implementation: `app/services/environmental_acquisition.py`
Acquisition entry points: `acquire_environmental_data_for_investigation()`, `acquire_environmental_data_for_scene()`

### Framing Parameters & Defaults
Stage C1 exposes explicit, configurable framing parameters with operational defaults:
- `spatial_buffer_degrees` (default `0.25`° ≈ 25–28 km): Expands the bounding box around the Sentinel-1 footprint or AOI to ensure wind and current coverage extends beyond the immediate scene boundary for subsequent analysis.
- `lookback_hours` (default `24.0` hours): Temporal lookback prior to scene acquisition (or window start) to capture preceding environmental conditions.
- `forward_hours` (default `6.0` hours): Forward duration following scene acquisition to capture succeeding environmental conditions.

> [!NOTE]
> These defaults are operational acquisition and framing baselines, NOT scientifically optimal drift-modelling horizons. Future stages (Stage D / Stage F) may override them based on specific simulation requirements.

Framing rules:
- Clamps derived bounding boxes strictly within valid geographic coordinate limits: $[-90.0, 90.0]$ latitude and $[-180.0, 180.0]$ longitude.
- Rejects inverted or degenerate bounding boxes.
- Rejects or flags dateline-crossing footprints ($W > E$), adhering to provider bounding box limitations.
- Preserves UTC timezone awareness and timestamp accuracy from the parent scene or investigation.

### Provider Integration & Acquisition Flow
- Reuses the existing `Era5AcquisitionProvider` and `CmemsAcquisitionProvider` classes through the common `AcquisitionProvider` abstraction.
- Formulates `AcquisitionRequest` contracts with `DataType.WIND` (ERA5) and `DataType.CURRENT` (CMEMS).
- ERA5 coordinates are formatted using North-West-South-East conventions (`[N, W, S, E]`); CMEMS bounding boxes use `[W, S, E, N]`.
- Provider-specific logic remains entirely encapsulated within their respective providers; Stage C1 acts solely as the orchestrator.

### A4 Scientific Validation Integration
Acquisition does NOT equate to scientific acceptance. Downloaded artifacts are validated prior to registration:
- ERA5 NetCDF files are validated using `Era5Validator`:
  - Required dimensions: `valid_time`/`time`, `latitude`, `longitude`.
  - Required variables: `u10`, `v10`.
  - Physical ranges: speeds within $[0, 100]$ m/s, strictly positive wind speed, non-empty temporal span.
  - Multi-file directory artifacts (generated by `cdsapi` when requests span calendar months) are unpacked and each `.nc` file validated individually.
- CMEMS NetCDF files are validated using `CmemsValidator`:
  - Required dimensions: `time`, `depth`, `latitude`, `longitude`.
  - Required variables: `uo`, `vo`.
  - Physical ranges: speeds within $[0, 10]$ m/s, surface depth $\le 5$ m, non-empty temporal span.
- Only artifacts that pass scientific validation are registered in the `AssetRegistry`. Unvalidated or rejected artifacts are excluded.

### Partial Failure & Deterministic Error Handling
- Providers operate independently; a failure in CMEMS acquisition does not abort ERA5 acquisition, and vice-versa.
- Each provider result is tracked explicitly with status (`ACQUIRED_AND_VALIDATED`, `ACQUIRED_VALIDATION_FAILED`, `ACQUISITION_FAILED`, `SKIPPED`), artifact path, asset ID, validation errors, and elapsed time.
- The pipeline returns an `EnvironmentalAcquisitionResult` summarizing both succeeded and failed providers without masking partial failures.

### Asset & Domain Model Integration
- Validated ERA5 wind artifacts are registered as `Asset` (`AssetType.WIND_FIELD`) and converted into `WindField` domain objects (`app.models.environmental.WindField`).
- Validated CMEMS current artifacts are registered as `Asset` (`AssetType.CURRENT_FIELD`) and converted into `CurrentField` domain objects (`app.models.environmental.CurrentField`).
- Provenance explicitly links generated assets to the parent `investigation_id`, `scene_id`, provider name, and source request parameters.

### API Endpoint
`POST /api/v1/investigations/{investigation_id}/environment/acquire`

Request payload:
```json
{
  "scene_id": "optional-scene-id",
  "spatial_buffer_degrees": 0.25,
  "lookback_hours": 24.0,
  "forward_hours": 6.0,
  "providers": ["era5", "cmems"]
}
```

Response:
```json
{
  "investigation_id": "inv-123",
  "scene_id": "optional-scene-id",
  "overall_success": true,
  "spatial_buffer_degrees": 0.25,
  "lookback_hours": 24.0,
  "forward_hours": 6.0,
  "environmental_bbox": [9.0, 42.0, 10.5, 43.5],
  "environmental_time_window": {
    "start": "2024-01-01T00:00:00Z",
    "end": "2024-01-02T06:00:00Z"
  },
  "providers": {
    "era5": {
      "provider": "era5",
      "status": "ACQUIRED_AND_VALIDATED",
      "data_type": "wind",
      "asset_id": "asset-...",
      "location": "/path/to/era5.nc",
      "validation_passed": true,
      "validation_errors": []
    },
    "cmems": {
      "provider": "cmems",
      "status": "ACQUIRED_AND_VALIDATED",
      "data_type": "current",
      "asset_id": "asset-...",
      "location": "/path/to/cmems.nc",
      "validation_passed": true,
      "validation_errors": []
    }
  },
  "succeeded_providers": ["era5", "cmems"],
  "failed_providers": []
}
```

### Scientific Limitations
- **Resolution & Grid Coarseness**: ERA5 reanalysis has a native spatial resolution of $\approx 31$ km ($0.25^\circ$) and 1-hour temporal resolution. CMEMS global physical reanalysis/analysis has a nominal $\approx 1/12^\circ$ resolution. Near-coastal sub-mesoscale eddies and complex topographic wind channeling are not fully resolved.
- **Surface Depth**: CMEMS surface currents represent average velocity over the top layer (0–0.5 m or 0–5 m depth). Wind-driven Stokes drift is not included unless explicitly modelled in downstream drift stages.
- **Framing Defaults**: A 24-hour lookback and 6-hour forward window are acquisition buffers, not simulation drift physics.
- **Stage Boundary**: C1 does NOT perform spatial/temporal interpolation, grid resampling, particle tracking, backward/forward drift trajectories, or AIS fusion (strictly reserved for Stages D, E, and F).

---

## Stage D1 — Forward Drift Modelling

### Overview
Stage D1 implements deterministic Leeway-Euler forward drift modelling for observed oil spill candidates. It consumes the spill centroid and acquisition time from Stage B3 (`SpillDetection`) along with the validated environmental forcing fields from Stage C1 (`ENVIRONMENT_WIND` via ERA5 and `ENVIRONMENT_CURRENT` via CMEMS), integrates the surface displacement forward in time, and registers a derived `DRIFT_PRODUCT` GeoJSON LineString in the `AssetRegistry`.

```
Observed SAR Spill (Stage B3) + Validated Metocean Assets (Stage C1)
       │                              │
       ├──────────────────────────────┘
       ▼
Deterministic Leeway-Euler Forward Integration
  v_drift(t) = v_current(lon, lat, t) + α · v_wind(lon, lat, t)
       │
       ▼
GeoJSON LineString Artifact (`drift_trajectory.geojson`)
       │
       ▼
AssetRegistry Registration (`AssetType.DRIFT_PRODUCT`) & `DriftResult`
```

### Governing Equations & Numerical Scheme
1. **Drift Velocity**:
   $$\vec{v}_{\text{drift}}(t) = \vec{v}_{\text{current}}(\text{lon}, \text{lat}, t) + \alpha \cdot \vec{v}_{\text{wind}}(\text{lon}, \text{lat}, t)$$
   where:
   - $\vec{v}_{\text{current}} = (u_o, v_o)$ from CMEMS near-surface layer ($\approx 0.5$ m depth)
   - $\vec{v}_{\text{wind}} = (u_{10}, v_{10})$ from ERA5 10 m reanalysis
   - $\alpha = 0.035$ (default leeway fraction: 3.5% of 10 m wind speed)

2. **Euler Forward Stepping**:
   $$\Delta t = \min(\text{step\_hours}, \text{remaining\_hours})$$
   $$\text{lon}_{\text{new}} = \text{lon} + \frac{u_{\text{drift}} \cdot \Delta t \cdot 3600}{\cos(\text{lat} \cdot \frac{\pi}{180}) \cdot 111320}$$
   $$\text{lat}_{\text{new}} = \text{lat} + \frac{v_{\text{drift}} \cdot \Delta t \cdot 3600}{111320}$$

3. **Spatial & Temporal Interpolation**:
   - Spatial interpolation: bilinear 2D interpolation using `scipy.interpolate.RegularGridInterpolator` pre-built and cached per variable/time-slice.
   - Temporal selection: nearest available temporal slice (ERA5: hourly; CMEMS: daily P1D).

### API Route
- **Endpoint**: `POST /api/v1/investigations/{investigation_id}/spills/{spill_id}/drift`
- **Request Body**:
  ```json
  {
    "wind_asset_id": "asset-era5-id",
    "current_asset_id": "asset-cmems-id",
    "drift_hours": 24.0,
    "step_hours": 1.0,
    "leeway_fraction": 0.035
  }
  ```
- **Response**: `DriftResult` object containing origin, steps with component velocities and cumulative distance, endpoint coordinates, and provenance metadata.

### Scientific Limitations & Assumptions
- **Deterministic Baseline**: Stage D1 is a deterministic baseline model, **not** an operational oil-spill forecast or source-attribution model.
- **Constant Leeway Fraction ($\alpha = 0.035$)**: 3.5% is an operational empirical baseline derived from ITOPF, NOAA GNOME, and Breivik et al. (2011). It is **not calibrated or tuned** for this project, specific oil types, slick thickness, or weathering/emulsification state.
- **Wind Forcing at 10 m**: ERA5 $10$ m wind is used as a proxy for surface atmospheric drag; wave-dependent surface roughness and wave-induced Stokes drift are not explicitly modelled.
- **CMEMS Near-Surface Proxy**: CMEMS GLORYS12V1 top level ($\approx 0.5$ m depth) represents bulk layer velocity; Langmuir circulation, sub-mesoscale turbulence, and wave-current interactions are excluded.
- **Temporal Resolution**: CMEMS data is daily (P1D); sub-daily current variability (e.g., tidal currents, inertial oscillations) is unresolved.
- **First-Order Integration**: Euler forward integration with flat-Earth projection is applied; suitable for regional trajectories (< ~500 km). Runge-Kutta 4th-order and geodesic stepping are reserved for Stage D2+.
- **No Stochastic Uncertainty**: Trajectories are purely deterministic; ensemble spread and spatial uncertainty (`endpoint_uncertainty_km = None`) are reserved for Stage D2.
- **No Backward Drift or Attribution**: Stage D1 models forward dispersion only; backward tracking and vessel attribution belong strictly to Stage D3, Stage E, and Stage F.






