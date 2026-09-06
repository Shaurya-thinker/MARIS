# MARIS Backend

This is the MARIS backend foundation.

The current purpose is to provide the initial API foundation, domain contracts,
and a data-acquisition framework. The HTTP surface is still a health endpoint
at `GET /health`. Sentinel-1 acquisition is a library provider, not an API route.

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

Tests mock CDSE HTTP. They do not require Copernicus credentials and they do
not download Sentinel-1 products.

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
