"""Sentinel-1 scene ingestion service for Stage B1.

Coordinates local Sentinel-1 ZIP artifact validation (A4.2), asset registration
(A3 AssetRegistry), and SatelliteScene creation (A2 domain model).

Read-only with respect to the source Sentinel-1 ZIP artifact.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from app.acquisition.registry import AssetRegistry, default_asset_registry
from app.acquisition.schemas import AcquiredArtifact
from app.models.asset import Asset
from app.models.common import AssetType, PolygonAreaOfInterest, Provenance
from app.models.satellite import SatelliteScene
from app.validation.schemas import ValidationResult, ValidationSeverity
from app.validation.sentinel1 import Sentinel1Validator


class Sentinel1IngestionError(Exception):
    """Raised when Sentinel-1 ingestion fails due to validation or formatting errors."""


def gml_coords_to_polygon(coords_raw: str) -> PolygonAreaOfInterest:
    """Convert extracted A4.2 GML footprint coordinates string to PolygonAreaOfInterest.

    GML coordinates format: lat,lon pairs separated by whitespace.
    GeoJSON / PolygonAreaOfInterest format: [longitude, latitude] position rings.

    Preserves WGS84 coordinates without modification, ensuring closed ring and
    correct [lon, lat] ordering expected by GeoJSON / domain model.
    """
    if not coords_raw or not isinstance(coords_raw, str) or not coords_raw.strip():
        raise Sentinel1IngestionError("Footprint coordinates string is empty or missing")

    tokens = coords_raw.strip().split()
    if len(tokens) < 3:
        raise Sentinel1IngestionError("Footprint coordinates contain fewer than 3 points")

    ring: list[list[float]] = []
    for token in tokens:
        parts = token.split(",")
        if len(parts) < 2:
            raise Sentinel1IngestionError(f"Malformed coordinate token '{token}'")
        try:
            lat = float(parts[0])
            lon = float(parts[1])
        except ValueError as exc:
            raise Sentinel1IngestionError(f"Non-numeric coordinate value in token '{token}': {exc}") from exc

        if not (-90.0 <= lat <= 90.0):
            raise Sentinel1IngestionError(f"Latitude {lat} outside valid range [-90, +90]")
        if not (-180.0 <= lon <= 180.0):
            raise Sentinel1IngestionError(f"Longitude {lon} outside valid range [-180, +180]")

        # GeoJSON position is [longitude, latitude]
        ring.append([lon, lat])

    # Ensure closed ring
    if ring[0] != ring[-1]:
        ring.append(list(ring[0]))

    if len(ring) < 4:
        raise Sentinel1IngestionError("Polygon exterior ring must have at least 4 positions")

    try:
        return PolygonAreaOfInterest(kind="polygon", coordinates=[ring])
    except Exception as exc:
        raise Sentinel1IngestionError(f"Failed to create PolygonAreaOfInterest: {exc}") from exc


def create_scene_from_asset_and_validation(
    asset: Asset,
    validation_result: ValidationResult,
    scene_id: str | None = None,
) -> SatelliteScene:
    """Construct a SatelliteScene from a registered Asset and ValidationResult."""
    meta = validation_result.metadata or {}
    coords_raw = meta.get("footprint_coords")
    if not coords_raw:
        raise Sentinel1IngestionError("Validation metadata is missing 'footprint_coords'")

    footprint_polygon = gml_coords_to_polygon(str(coords_raw))

    sensing_start_raw = meta.get("sensing_start")
    acq_time: datetime | None = None
    if sensing_start_raw:
        try:
            cleaned = str(sensing_start_raw).strip().replace(" ", "T")
            cleaned = cleaned.replace("Z", "+00:00")
            if not cleaned.endswith("+00:00") and "+" not in cleaned and cleaned.count("-") <= 2:
                cleaned += "+00:00"
            acq_time = datetime.fromisoformat(cleaned)
            if acq_time.tzinfo is None:
                acq_time = acq_time.replace(tzinfo=timezone.utc)
        except Exception:
            acq_time = None

    if acq_time is None:
        acq_time = asset.acquisition_time or datetime.now(timezone.utc)

    platform = meta.get("platform", "SENTINEL-1")
    instrument = meta.get("instrument", "SAR")
    sensor = f"{platform} {instrument}".strip()

    scene_metadata: dict[str, Any] = {
        "product_name": meta.get("product_name"),
        "product_type": meta.get("product_type"),
        "sensor_mode": meta.get("sensor_mode"),
        "polarisation": meta.get("polarisation"),
        "sensing_start": meta.get("sensing_start"),
        "sensing_stop": meta.get("sensing_stop"),
        "orbit_number": meta.get("orbit_number"),
        "validation_classification": meta.get("validation_classification"),
    }
    scene_metadata = {k: v for k, v in scene_metadata.items() if v is not None}

    return SatelliteScene(
        id=scene_id or str(uuid4()),
        investigation_id=asset.investigation_id,
        asset_id=asset.id,
        provider=asset.provider,
        sensor=sensor,
        acquisition_time=acq_time,
        footprint=footprint_polygon,
        resolution=None,
        metadata=scene_metadata,
    )


def ingest_sentinel1_artifact(
    investigation_id: str,
    artifact_path: str | Path,
    provider_id: str = "sentinel1",
    registry: AssetRegistry | None = None,
) -> tuple[SatelliteScene, ValidationResult, Asset]:
    """Ingest an existing local Sentinel-1 ZIP artifact into MARIS.

    Process:
    1. Verify file exists and is accessible.
    2. Run A4.2 Sentinel1Validator.
    3. Fail closed if validation fails (passed=False).
    4. Register Asset in AssetRegistry.
    5. Construct and return SatelliteScene, ValidationResult, and Asset.
    """
    path = Path(artifact_path)
    if not path.exists():
        raise Sentinel1IngestionError(f"Sentinel-1 artifact file does not exist: {path}")
    if not path.is_file():
        raise Sentinel1IngestionError(f"Sentinel-1 artifact path is not a file: {path}")

    target_registry = registry or default_asset_registry

    retrieved_at = datetime.now(timezone.utc)
    acquired_artifact = AcquiredArtifact(
        asset_type=AssetType.SATELLITE_SCENE,
        location=str(path.resolve()),
        source="local_file",
        acquisition_time=retrieved_at,
        provenance=Provenance(
            product_id=path.name,
            retrieved_at=retrieved_at,
            notes="Local Sentinel-1 ZIP artifact ingested into MARIS Stage B1.",
        ),
        metadata={"provider": provider_id, "file_name": path.name},
    )

    validator = Sentinel1Validator()
    validation_result = validator.validate(acquired_artifact)

    if not validation_result.passed:
        error_issues = [i for i in validation_result.issues if i.severity == ValidationSeverity.ERROR]
        msg = "; ".join(f"[{i.code}] {i.message}" for i in error_issues) or "Validation failed"
        raise Sentinel1IngestionError(f"Sentinel-1 artifact validation failed: {msg}")

    meta = validation_result.metadata or {}
    sensing_start_raw = meta.get("sensing_start")
    if sensing_start_raw:
        try:
            cleaned = str(sensing_start_raw).strip().replace(" ", "T").replace("Z", "+00:00")
            if not cleaned.endswith("+00:00") and "+" not in cleaned and cleaned.count("-") <= 2:
                cleaned += "+00:00"
            acq_dt = datetime.fromisoformat(cleaned)
            if acq_dt.tzinfo is None:
                acq_dt = acq_dt.replace(tzinfo=timezone.utc)
            acquired_artifact = acquired_artifact.model_copy(update={"acquisition_time": acq_dt})
        except Exception:
            pass

    asset = target_registry.register(investigation_id, provider_id, acquired_artifact)
    scene = create_scene_from_asset_and_validation(asset, validation_result)

    return scene, validation_result, asset
