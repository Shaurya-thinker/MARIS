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

