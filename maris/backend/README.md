# MARIS Backend

This is the MARIS backend foundation.

The current purpose is to provide the initial API foundation, domain contracts,
and a data-acquisition framework. The HTTP surface is still a health endpoint at `GET /health`. Sentinel-1, ERA5,
and CMEMS acquisition are library providers, not API routes.

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

Tests mock CDSE, CDS, and Copernicus Marine HTTP. They do not require Copernicus
credentials and they do not download Sentinel-1 products, ERA5 NetCDF, or CMEMS
NetCDF files.

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
