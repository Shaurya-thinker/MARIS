import os
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip()


class Settings:
    service_name = "MARIS backend"

    def __init__(self, **overrides: object) -> None:
        backend_root = Path(__file__).resolve().parents[2]
        self.service_name = _env("MARIS_SERVICE_NAME", "MARIS backend")
        self.data_dir = Path(_env("MARIS_DATA_DIR", str(backend_root / "data")))
        self.cdse_username = _env("CDSE_USERNAME")
        self.cdse_password = _env("CDSE_PASSWORD")
        self.cdse_totp = _env("CDSE_TOTP")
        self.cdse_access_token = _env("CDSE_ACCESS_TOKEN")
        self.cdse_client_id = _env("CDSE_CLIENT_ID", "cdse-public")
        self.cdse_token_url = _env(
            "CDSE_TOKEN_URL",
            "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
        )
        self.cdse_catalogue_url = _env(
            "CDSE_CATALOGUE_URL",
            "https://catalogue.dataspace.copernicus.eu/odata/v1",
        )
        self.cdse_download_url = _env(
            "CDSE_DOWNLOAD_URL",
            "https://zipper.dataspace.copernicus.eu/odata/v1",
        )
        for key, value in overrides.items():
            setattr(self, key, value)


settings = Settings()
