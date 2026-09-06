from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from uuid import uuid4

from app.acquisition.schemas import AcquiredArtifact, AcquisitionRequest, AcquisitionResult
from app.models.asset import Asset
from app.models.investigation import Investigation


class AssetRegistry(ABC):
    """Turns acquired artifacts into Stage A2 Asset records.

    Persistence backends are out of scope; this only defines registration.
    """

    @abstractmethod
    def register(
        self,
        investigation_id: str,
        provider_id: str,
        artifact: AcquiredArtifact,
    ) -> Asset:
        """Create and store an Asset from a provider artifact."""

    @abstractmethod
    def get(self, asset_id: str) -> Asset:
        """Return a previously registered asset."""

    @abstractmethod
    def list_for_investigation(self, investigation_id: str) -> list[Asset]:
        """Return assets registered for an investigation, in registration order."""

    def register_result(
        self,
        request: AcquisitionRequest,
        result: AcquisitionResult,
    ) -> list[Asset]:
        return [
            self.register(request.investigation_id, request.provider_id, artifact)
            for artifact in result.artifacts
        ]

    def attach_to_investigation(self, investigation: Investigation, assets: Sequence[Asset]) -> Investigation:
        """Return a copy of the investigation with asset ids appended. No I/O."""
        existing = list(investigation.asset_ids)
        for asset in assets:
            if asset.investigation_id != investigation.id:
                raise ValueError(
                    f"asset '{asset.id}' belongs to investigation '{asset.investigation_id}', "
                    f"not '{investigation.id}'"
                )
            if asset.id not in existing:
                existing.append(asset.id)
        return investigation.model_copy(update={"asset_ids": existing})

    def asset_from_artifact(
        self,
        asset_id: str,
        investigation_id: str,
        provider_id: str,
        artifact: AcquiredArtifact,
    ) -> Asset:
        provenance = artifact.provenance
        extra = dict(provenance.extra)
        extra.setdefault("provider_id", provider_id)
        retrieved_at = provenance.retrieved_at or artifact.acquisition_time
        provenance = provenance.model_copy(update={"extra": extra, "retrieved_at": retrieved_at})
        return Asset(
            id=asset_id,
            investigation_id=investigation_id,
            type=artifact.asset_type,
            provider=provider_id,
            source=artifact.source,
            location=artifact.location,
            acquisition_time=artifact.acquisition_time,
            provenance=provenance,
            metadata=dict(artifact.metadata),
        )


class InMemoryAssetRegistry(AssetRegistry):
    """Process-local asset index. Not a database."""

    def __init__(self, id_factory: Callable[[], str] | None = None) -> None:
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._assets: dict[str, Asset] = {}
        self._order: list[str] = []

    def register(
        self,
        investigation_id: str,
        provider_id: str,
        artifact: AcquiredArtifact,
    ) -> Asset:
        asset = self.asset_from_artifact(
            asset_id=self._id_factory(),
            investigation_id=investigation_id,
            provider_id=provider_id,
            artifact=artifact,
        )
        self._assets[asset.id] = asset
        self._order.append(asset.id)
        return asset

    def get(self, asset_id: str) -> Asset:
        try:
            return self._assets[asset_id]
        except KeyError as error:
            raise KeyError(f"unknown asset '{asset_id}'") from error

    def list_for_investigation(self, investigation_id: str) -> list[Asset]:
        return [
            self._assets[asset_id]
            for asset_id in self._order
            if self._assets[asset_id].investigation_id == investigation_id
        ]
